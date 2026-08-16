"""Two independent checks every Claude-produced artifact must pass before
it can reach the renderer:

1. `validate_schema` — structural validation against schemas/*.json.
2. `run_examples` — actually executes the generated Python solution against
   the problem's official examples. Claude's own adversarial verification
   pass (prompts/claude/v1/verify.md) catches most reasoning errors, but
   only running the code catches the rest (see ARCHITECTURE.md "Failure
   policy": "Never publish unverified code").

`run_examples` is a best-effort generic harness, not a full LeetCode judge:
it handles the common single-method signature shape ("Input: nums = [...],
target = 9") by matching example variable names to the solution method's
parameter names. It does NOT handle "design" problems whose examples encode
a sequence of method calls (e.g. LRUCache-style
`["LRUCache","put","get"], [[2],[1,1],[1]]`) — those are detected and
skipped with a warning rather than crashing the run, since building a full
call-sequence interpreter is out of scope for v1.
"""

from __future__ import annotations

import ast
import inspect
import logging
import re
import signal
from dataclasses import dataclass, field

import jsonschema

from src.leetcode.models import Example

log = logging.getLogger(__name__)

_ASSIGNMENT_SPLIT_RE = re.compile(r",\s*(?=[A-Za-z_][A-Za-z0-9_]*\s*=)")
_DESIGN_PROBLEM_INPUT_RE = re.compile(r'^\s*\[\s*"[A-Z]\w*"')
_JS_LITERAL_RE = re.compile(r'"[^"]*"|\'[^\']*\'|\b(true|false|null)\b')
_JS_TO_PY_LITERAL = {"true": "True", "false": "False", "null": "None"}


def _normalize_js_literals(text: str) -> str:
    """LeetCode renders example input/output using JS/JSON literal spelling
    (`true`, `false`, `null`), which `ast.literal_eval` doesn't recognize --
    it parses them as `ast.Name` nodes and raises "malformed node or string".
    Swap in the Python spellings, but only outside quoted string literals so
    a string value like "true story" is left untouched."""
    return _JS_LITERAL_RE.sub(
        lambda m: _JS_TO_PY_LITERAL[m.group(1)] if m.group(1) else m.group(0), text
    )


class ValidationError(RuntimeError):
    """Raised on schema validation failure. Message includes the jsonschema
    error path for fast debugging."""


class ExampleExecutionError(RuntimeError):
    """Raised when the generated solution fails an official example."""


@dataclass
class ExampleRunReport:
    total: int = 0
    passed: int = 0
    skipped: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


def validate_schema(data: dict, schema: dict) -> None:
    try:
        jsonschema.validate(instance=data, schema=schema)
    except jsonschema.ValidationError as exc:
        path = " -> ".join(str(p) for p in exc.absolute_path) or "(root)"
        raise ValidationError(f"Schema validation failed at {path}: {exc.message}") from exc


def clamp_to_schema(data: dict, schema: dict) -> int:
    """Truncates in place any string in `data` that overshoots its schema's
    `maxLength`, walking `$ref`/`$defs`/`oneOf` the same way the real schema
    does. Returns the number of fields clamped (0 if `data` already fit).

    Call this *before* `validate_schema()` on solve/compress output. Claude
    has no tool access in those stages (see ARCHITECTURE.md "Why the
    prompts explicitly say 'no tool access'") and cannot precisely count
    characters against a hard limit like `reasoning_panel.bullets[i]`'s 120
    -- a model landing a few characters over a length budget is expected,
    recoverable drift, not a reasoning failure worth discarding an entire
    (paid) generation over and re-running the whole stage. This applies the
    same fix a human copy-editor would: trim to fit, don't regenerate.

    Deliberately narrow in scope: only `maxLength` overshoot is repaired
    this way. Every other constraint (`enum`, `const`, `required`,
    `minLength`, item-count bounds) still reaches `validate_schema()`
    untouched and fails normally, since those indicate a real shape or
    reasoning problem rather than a length overshoot.
    """
    return _clamp_object(data, schema, root=schema)


def _resolve_schema(node: dict, root: dict) -> dict:
    if "$ref" in node:
        def_name = node["$ref"].rsplit("/", 1)[-1]
        return root.get("$defs", {})[def_name]
    return node


def _pick_one_of_branch(value: object, branches: list[dict], root: dict) -> dict | None:
    """Diagram items are `oneOf` two shapes distinguished by a `const`
    discriminator field (`component`). Picks the branch whose consts match
    `value` so clamping can recurse into the right per-branch maxLengths;
    returns None (no clamping attempted) if no branch matches, leaving the
    mismatch for `validate_schema()` to report precisely."""
    if not isinstance(value, dict):
        return None
    for branch in branches:
        resolved = _resolve_schema(branch, root)
        consts = {
            key: prop["const"]
            for key, prop in resolved.get("properties", {}).items()
            if "const" in prop
        }
        if consts and all(value.get(key) == expected for key, expected in consts.items()):
            return resolved
    return None


def _truncate_string(value: str, max_length: int) -> str:
    ellipsis = "…"
    if max_length <= len(ellipsis):
        return value[:max_length]
    return value[: max_length - len(ellipsis)].rstrip() + ellipsis


def _clamp_object(obj: dict, schema: dict, root: dict) -> int:
    if not isinstance(obj, dict):
        return 0
    clamped = 0
    properties = schema.get("properties", {})
    for key, value in obj.items():
        prop_schema = properties.get(key)
        if prop_schema is not None:
            clamped += _clamp_value(obj, key, value, prop_schema, root)
    return clamped


def _clamp_array(arr: list, item_schema: dict, root: dict) -> int:
    clamped = 0
    for i, item in enumerate(arr):
        clamped += _clamp_value(arr, i, item, item_schema, root)
    return clamped


def _clamp_value(container, key, value: object, schema: dict, root: dict) -> int:
    schema = _resolve_schema(schema, root)

    if "oneOf" in schema:
        branch = _pick_one_of_branch(value, schema["oneOf"], root)
        if branch is None:
            return 0
        schema = branch

    schema_type = schema.get("type")

    if isinstance(value, dict) and (schema_type == "object" or "properties" in schema):
        return _clamp_object(value, schema, root)

    if isinstance(value, list) and schema_type == "array" and "items" in schema:
        return _clamp_array(value, schema["items"], root)

    if isinstance(value, str):
        max_length = schema.get("maxLength")
        if max_length is not None and len(value) > max_length:
            truncated = _truncate_string(value, max_length)
            log.warning(
                "Clamped an over-length string from %d to %d chars to fit the schema's "
                "maxLength (model overshot a character budget it has no tool access to "
                "precisely count): %r -> %r",
                len(value),
                max_length,
                value,
                truncated,
            )
            container[key] = truncated
            return 1

    return 0


class _TimeoutGuard:
    """Best-effort per-example execution timeout using SIGALRM. Unix-only
    (fine for GitHub Actions' ubuntu-latest runners); on platforms without
    SIGALRM this becomes a no-op rather than crashing."""

    def __init__(self, seconds: int):
        self.seconds = seconds
        self._supported = hasattr(signal, "SIGALRM")

    def __enter__(self):
        if self._supported:
            signal.signal(signal.SIGALRM, self._on_alarm)
            signal.alarm(self.seconds)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._supported:
            signal.alarm(0)
        return False

    def _on_alarm(self, signum, frame):
        raise TimeoutError(f"Example execution exceeded {self.seconds}s")


def _parse_assignments(input_str: str) -> dict:
    """Parses LeetCode's "var = value, var2 = value2" example input format
    into a namespace dict. Splits only on commas that precede a new
    `identifier =`, so commas inside list/dict literals are preserved."""
    lines = _ASSIGNMENT_SPLIT_RE.split(input_str.strip())
    namespace: dict = {}
    for line in lines:
        line = line.strip()
        if not line or "=" not in line:
            continue
        name, _, value_expr = line.partition("=")
        name = name.strip()
        try:
            namespace[name] = ast.literal_eval(_normalize_js_literals(value_expr.strip()))
        except (ValueError, SyntaxError) as exc:
            raise ExampleExecutionError(
                f"Could not parse example input fragment '{line}': {exc}"
            ) from exc
    return namespace


def _find_solution_class(code: str) -> type:
    namespace: dict = {}
    try:
        exec(compile(code, "<generated_solution>", "exec"), namespace)  # noqa: S102
    except Exception as exc:  # noqa: BLE001 - any failure here is a real validation failure
        raise ExampleExecutionError(f"Generated code failed to execute/import: {exc}") from exc

    solution_cls = namespace.get("Solution")
    if solution_cls is None or not inspect.isclass(solution_cls):
        raise ExampleExecutionError("Generated code does not define a `Solution` class")
    return solution_cls


def _find_entry_method(solution_cls: type):
    methods = [
        name
        for name, member in inspect.getmembers(solution_cls, predicate=inspect.isfunction)
        if not name.startswith("_")
    ]
    if not methods:
        raise ExampleExecutionError("Solution class defines no public method")
    if len(methods) > 1:
        log.warning(
            "Solution class defines multiple public methods (%s); using the first one. "
            "If this is wrong, prompts/claude/v1/solve.md may need a stronger "
            "single-method constraint.",
            methods,
        )
    return getattr(solution_cls, methods[0])


def run_examples(code: str, examples: list[Example], timeout_seconds: int = 5) -> ExampleRunReport:
    report = ExampleRunReport(total=len(examples))

    solution_cls = _find_solution_class(code)
    method = _find_entry_method(solution_cls)
    param_names = [p for p in inspect.signature(method).parameters if p != "self"]

    for i, example in enumerate(examples, start=1):
        label = f"example {i}"
        if _DESIGN_PROBLEM_INPUT_RE.match(example.input):
            report.skipped.append(
                f"{label}: looks like a design-problem call sequence, not a single-call "
                "example — skipping automated execution (manual review recommended)"
            )
            continue

        try:
            namespace = _parse_assignments(example.input)
            expected = ast.literal_eval(_normalize_js_literals(example.output.strip()))

            args = [namespace[name] for name in param_names if name in namespace]
            if len(args) != len(param_names):
                report.skipped.append(
                    f"{label}: could not map example variables {list(namespace)} to "
                    f"method parameters {param_names} — skipping"
                )
                continue

            with _TimeoutGuard(timeout_seconds):
                instance = solution_cls()
                actual = method(instance, *args)

            if actual == expected:
                report.passed += 1
            else:
                report.failures.append(f"{label}: expected {expected!r}, got {actual!r}")
        except ExampleExecutionError as exc:
            report.failures.append(f"{label}: {exc}")
        except TimeoutError as exc:
            report.failures.append(f"{label}: {exc}")
        except Exception as exc:  # noqa: BLE001 - surface as a normal failure, not a crash
            report.failures.append(f"{label}: raised {type(exc).__name__}: {exc}")

    return report
