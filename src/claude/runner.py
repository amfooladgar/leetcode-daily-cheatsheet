"""Runs a single Claude Code stage headlessly and returns parsed JSON.

Every call shells out to:

    claude -p --bare --output-format json --json-schema <schema> \\
        --model <model> --max-turns <n> --allowedTools <tools>

`--bare` is deliberate (see ARCHITECTURE.md "Why --bare"): production runs
must not depend on anyone's local CLAUDE.md, hooks, or MCP servers. Every
byte of the prompt comes from prompts/claude/<version>/<stage>.md, filled in
by this module, so a run is fully reproducible from the files in this repo
plus the ANTHROPIC_API_KEY environment variable.

If `CLAUDE_CODE_OAUTH_TOKEN` is also set (a long-lived token from
`claude setup-token`, tied to a Claude Pro/Max subscription -- see
ARCHITECTURE.md "Claude Pro/Max fallback auth"), a stage that fails with
Anthropic's own "credit balance is too low" billing rejection is retried
exactly once with `ANTHROPIC_API_KEY` stripped from that one subprocess's
environment. That retry also drops `--bare`: per `claude -p --help`,
`--bare` forces auth to strictly ANTHROPIC_API_KEY/apiKeyHelper and *never*
reads OAuth, so there is no way to reach the subscription token without
dropping it. This means the one fallback call also auto-loads this repo's
checked-out CLAUDE.md/hooks/MCP config -- a real, deliberate exception to
the reproducibility guarantee described in ARCHITECTURE.md "Why --bare",
scoped to only the rare billing-outage retry. Any other failure (bad
solution, verify rejection, max_turns, timeout, ...) is never retried here
-- that would silently paper over a real bug instead of routing around a
billing outage.

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
import os
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


def _is_low_credit_failure(stdout: str) -> bool:
    """True only for Anthropic's own "credit balance is too low" billing
    rejection (api_error_status 400) -- the one failure mode the
    CLAUDE_CODE_OAUTH_TOKEN fallback below is allowed to retry. Every other
    failure (bad solution, verify rejection, max_turns, ...) must surface
    immediately, not get silently re-run under different auth."""
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return False
    reason = str(payload.get("result") or "")
    return "credit balance is too low" in reason.lower()


def _summarize_failed_payload(stdout: str) -> str | None:
    """Best-effort one-line summary of a failed stage's cost, terminal
    reason, and any denied tool-use attempts. Returns None if stdout isn't
    the JSON payload `claude -p` normally emits (nothing to summarize)."""
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    cost = payload.get("total_cost_usd")
    reason = payload.get("terminal_reason") or payload.get("result")
    denied = payload.get("permission_denials") or []
    parts = []
    if reason:
        parts.append(f"terminal_reason={reason!r}")
    if cost is not None:
        parts.append(f"cost=${cost:.4f}")
    if denied:
        tools = ", ".join(sorted({d.get("tool_name", "?") for d in denied}))
        parts.append(f"{len(denied)} denied tool-use attempt(s) ({tools})")
    return "(" + ", ".join(parts) + ")" if parts else None


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
    if not allowed_tools:
        # --allowedTools "" only denies *permission* to use a tool -- the
        # model's built-in Bash/Edit/Read/etc. roster is still listed as
        # "available" by default, so a pure-reasoning stage would still see
        # Bash and attempt it, burn a real (billed) turn on the denial, and
        # sometimes repeat this until max_turns is exhausted with zero
        # usable output (observed: a compress-stage run spent ~$0.07 across
        # 4 denied Bash attempts trying to verify a character count, then
        # failed outright). --tools "" removes the tool roster itself, so
        # the model never perceives a tool as available and never attempts
        # one -- see ARCHITECTURE.md "Why the prompts explicitly say 'no
        # tool access'".
        cmd += ["--tools", ""]

    log.info(
        "Running Claude stage '%s' (model=%s, prompt_version=%s)", stage, model, prompt_version
    )

    def _invoke(env: dict[str, str] | None, cmd_to_run: list[str]) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                cmd_to_run,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise ClaudeStageError(f"Stage '{stage}' timed out after {timeout_seconds}s") from exc
        except FileNotFoundError as exc:
            raise ClaudeStageError(
                f"'{claude_bin}' not found on PATH — install Claude Code first (see docs/SETUP.md)."
            ) from exc

    proc = _invoke(None, cmd)

    oauth_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if proc.returncode != 0 and oauth_token and _is_low_credit_failure(proc.stdout):
        log.warning(
            "Stage '%s' hit Anthropic's 'credit balance is too low' error -- "
            "retrying once via CLAUDE_CODE_OAUTH_TOKEN (Claude Pro/Max subscription) "
            "instead of ANTHROPIC_API_KEY. This retry also drops --bare (OAuth is "
            "never read in --bare mode), so this one call auto-loads this repo's "
            "checked-out CLAUDE.md/hooks/MCP config -- see ARCHITECTURE.md "
            "'Claude Pro/Max fallback auth'.",
            stage,
        )
        fallback_env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        fallback_cmd = [arg for arg in cmd if arg != "--bare"]
        proc = _invoke(fallback_env, fallback_cmd)

    if proc.returncode != 0:
        # Surface stdout too, not just stderr: some `claude` CLI failure
        # modes (a crash inside its own --json-schema handling, for
        # instance) exit non-zero with an empty stderr and put whatever
        # diagnostic text exists on stdout instead. Showing only stderr in
        # that case silently drops the one clue available for debugging.
        stderr = proc.stderr.strip() or "(empty)"
        stdout = proc.stdout.strip()
        detail = f"Stage '{stage}' exited {proc.returncode}.\n--- stderr ---\n{stderr}"
        # stdout is usually still valid JSON even on failure (e.g. a
        # max_turns exhaustion) -- pull out the cost/denial/reason fields so
        # a burned-budget-for-nothing failure is diagnosable from the log
        # line alone, without re-running and manually reading raw JSON.
        summary = _summarize_failed_payload(stdout)
        if summary:
            detail = (
                f"Stage '{stage}' exited {proc.returncode}. {summary}\n--- stderr ---\n{stderr}"
            )
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
