"""Runs a single Claude Code stage headlessly and returns parsed JSON.

Every call shells out to:

    claude -p --bare --output-format json --json-schema <schema> \\
        --model <model> --max-turns <n> --allowedTools <tools>

`--bare` is deliberate (see ARCHITECTURE.md "Why --bare"): production runs
must not depend on anyone's local CLAUDE.md, hooks, or MCP servers. Every
byte of the prompt comes from prompts/claude/<version>/<stage>.md, filled in
by this module, so a run is fully reproducible from the files in this repo
plus the ANTHROPIC_API_KEY environment variable.

The `schema_filename` callers pass here (src/main.py) is deliberately the
*simplified* generation schema under schemas/generation/, not the full
schemas/<stage>.schema.json -- see ARCHITECTURE.md "Why two schema files
per stage". `--json-schema` feeds into Claude's structured-output/tool
mechanism, which does not support the full JSON Schema spec ($ref/$defs,
oneOf/anyOf/allOf are unsupported or crash-prone); the full, precise shape
is enforced separately, after generation, via src/claude/validator.py's
validate_schema() against the real schema file.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts" / "claude"
SCHEMAS_DIR = Path(__file__).resolve().parent.parent.parent / "schemas"


class ClaudeStageError(RuntimeError):
    """Raised when a `claude -p` invocation fails or returns output that
    doesn't parse as JSON. The caller decides retry policy (see
    ARCHITECTURE.md "Failure policy") — this module does not retry itself,
    so each attempt is visible in the caller's logs."""


@dataclass
class ClaudeResult:
    structured_output: dict
    cost_usd: float | None
    session_id: str | None
    raw: dict


def _load_prompt(stage: str, prompt_version: str, **substitutions: str) -> str:
    path = PROMPTS_DIR / prompt_version / f"{stage}.md"
    if not path.exists():
        raise ClaudeStageError(f"No prompt file at {path}")
    text = path.read_text()
    for key, value in substitutions.items():
        text = text.replace(f"{{{{{key}}}}}", value)
    return text


def _load_schema_json(schema_filename: str) -> str:
    path = SCHEMAS_DIR / schema_filename
    if not path.exists():
        raise ClaudeStageError(f"No schema file at {path}")
    return path.read_text()


def run_stage(
    *,
    stage: str,
    schema_filename: str,
    model: str,
    max_turns: int,
    allowed_tools: str,
    prompt_version: str,
    claude_bin: str = "claude",
    timeout_seconds: int = 180,
    **prompt_substitutions: str,
) -> ClaudeResult:
    """Runs one prompts/claude/<prompt_version>/<stage>.md through
    `claude -p --bare` with the paired schemas/<schema_filename> enforced via
    --json-schema, and returns the structured_output plus cost/session
    metadata. Raises ClaudeStageError on any failure — callers decide
    whether/how to retry."""

    prompt = _load_prompt(stage, prompt_version, **prompt_substitutions)
    schema = _load_schema_json(schema_filename)

    cmd = [
        claude_bin,
        "-p",
        prompt,
        "--bare",
        "--output-format",
        "json",
        "--json-schema",
        schema,
        "--model",
        model,
        "--max-turns",
        str(max_turns),
    ]
    # Empty string is a valid, deliberate value: pure reasoning, no tool use.
    cmd += ["--allowedTools", allowed_tools]

    log.info(
        "Running Claude stage '%s' (model=%s, prompt_version=%s)", stage, model, prompt_version
    )
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ClaudeStageError(f"Stage '{stage}' timed out after {timeout_seconds}s") from exc
    except FileNotFoundError as exc:
        raise ClaudeStageError(
            f"'{claude_bin}' not found on PATH — install Claude Code first (see docs/SETUP.md)."
        ) from exc

    if proc.returncode != 0:
        # Surface stdout too, not just stderr: some `claude` CLI failure
        # modes (a crash inside its own --json-schema handling, for
        # instance) exit non-zero with an empty stderr and put whatever
        # diagnostic text exists on stdout instead. Showing only stderr in
        # that case silently drops the one clue available for debugging.
        stderr = proc.stderr.strip() or "(empty)"
        stdout = proc.stdout.strip()
        detail = f"Stage '{stage}' exited {proc.returncode}.\n--- stderr ---\n{stderr}"
        if stdout:
            detail += f"\n--- stdout (first 4000 chars) ---\n{stdout[:4000]}"
        raise ClaudeStageError(detail)

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ClaudeStageError(
            f"Stage '{stage}' did not return valid JSON on stdout:\n{proc.stdout[:2000]}"
        ) from exc

    if payload.get("is_error"):
        raise ClaudeStageError(f"Stage '{stage}' reported an error: {payload.get('result')}")

    structured = payload.get("structured_output")
    if structured is None:
        raise ClaudeStageError(
            f"Stage '{stage}' returned no structured_output — check the "
            "--json-schema was accepted. Raw payload keys: "
            f"{list(payload.keys())}"
        )

    return ClaudeResult(
        structured_output=structured,
        cost_usd=payload.get("total_cost_usd"),
        session_id=payload.get("session_id"),
        raw=payload,
    )
