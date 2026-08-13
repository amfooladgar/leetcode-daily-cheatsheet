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
            namespace[name] = ast.literal_eval(value_expr.strip())
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
            expected = ast.literal_eval(example.output.strip())

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
