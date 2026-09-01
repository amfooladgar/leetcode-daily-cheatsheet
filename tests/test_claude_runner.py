"""Unit tests for src/claude/runner.py's subprocess invocation and failure
diagnostics -- no network access, no API keys, subprocess.run is mocked."""

import json
import subprocess
import unittest
from unittest import mock

from src.claude.runner import ClaudeStageError, run_stage

_LOW_CREDIT_STDOUT = json.dumps(
    {
        "is_error": True,
        "terminal_reason": "api_error",
        "api_error_status": 400,
        "result": "Credit balance is too low",
        "total_cost_usd": 0,
    }
)
_MAX_TURNS_STDOUT = json.dumps(
    {"is_error": True, "terminal_reason": "max_turns", "total_cost_usd": 0.07}
)
_OK_STDOUT = json.dumps(
    {"structured_output": {"ok": True}, "total_cost_usd": 0.01, "session_id": "s1"}
)


def _completed(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["claude"], returncode=returncode, stdout=stdout, stderr=""
    )


class RunStageToolSuppressionTests(unittest.TestCase):
    """--allowedTools "" only denies permission to use a tool -- the model
    still sees the full built-in tool roster as "available" and can burn a
    real, billed turn attempting (then getting denied) one. --tools ""
    removes the roster itself so a pure-reasoning stage never perceives a
    tool as available in the first place."""

    def _run(self, allowed_tools: str) -> list[str]:
        ok_payload = json.dumps(
            {"structured_output": {"ok": True}, "total_cost_usd": 0.01, "session_id": "s1"}
        )
        with mock.patch("subprocess.run", return_value=_completed(ok_payload)) as mock_run:
            run_stage(
                stage="solve",
                schema_filename="generation/solve.gen-schema.json",
                model="claude-sonnet-5",
                max_turns=8,
                allowed_tools=allowed_tools,
                prompt_version="v1",
                problem_json="{}",
            )
        return mock_run.call_args[0][0]

    def test_empty_allowed_tools_also_suppresses_the_tool_roster(self):
        cmd = self._run("")
        self.assertIn("--allowedTools", cmd)
        self.assertEqual(cmd[cmd.index("--allowedTools") + 1], "")
        self.assertIn("--tools", cmd)
        self.assertEqual(cmd[cmd.index("--tools") + 1], "")

    def test_nonempty_allowed_tools_leaves_the_roster_alone(self):
        cmd = self._run("Bash(git *)")
        self.assertIn("--allowedTools", cmd)
        self.assertEqual(cmd[cmd.index("--allowedTools") + 1], "Bash(git *)")
        self.assertNotIn("--tools", cmd)


class RunStageFailureDiagnosticsTests(unittest.TestCase):
    """A failed stage's error message should surface cost/terminal_reason/
    denied-tool-attempt info from the JSON on stdout, not just the raw
    dump -- this is what let us diagnose the max_turns/wasted-cost failure
    without manually re-reading raw JSON."""

    def _run_and_capture_error(self, stdout: str, returncode: int = 1) -> str:
        with (
            mock.patch("subprocess.run", return_value=_completed(stdout, returncode)),
            self.assertRaises(ClaudeStageError) as ctx,
        ):
            run_stage(
                stage="compress",
                schema_filename="generation/cheatsheet.gen-schema.json",
                model="claude-sonnet-5",
                max_turns=8,
                allowed_tools="",
                prompt_version="v1",
                problem_json="{}",
                verified_solve_json="{}",
                headline_max_words="8",
                problem_summary_max_words="30",
                intuition_max_words="30",
                approach_max_steps="5",
                approach_max_words_per_step="15",
                example_max_states="5",
                code_preferred_max_lines="20",
                code_absolute_max_lines="30",
            )
        return str(ctx.exception)

    def test_max_turns_exhaustion_with_denied_tool_use_is_summarized(self):
        payload = json.dumps(
            {
                "is_error": True,
                "terminal_reason": "max_turns",
                "total_cost_usd": 0.0701255,
                "permission_denials": [
                    {"tool_name": "Bash"},
                    {"tool_name": "Bash"},
                ],
            }
        )
        message = self._run_and_capture_error(payload)
        self.assertIn("terminal_reason='max_turns'", message)
        self.assertIn("cost=$0.0701", message)
        self.assertIn("2 denied tool-use attempt(s) (Bash)", message)

    def test_non_json_stdout_falls_back_to_raw_dump_without_crashing(self):
        message = self._run_and_capture_error("not json at all")
        self.assertIn("Stage 'compress' exited 1", message)
        self.assertIn("not json at all", message)


class RunStageOAuthFallbackTests(unittest.TestCase):
    """A stage that fails with Anthropic's own 'credit balance is too low'
    billing rejection retries exactly once with ANTHROPIC_API_KEY stripped,
    if and only if CLAUDE_CODE_OAUTH_TOKEN is set -- see ARCHITECTURE.md
    'Claude Pro/Max fallback auth'. Every other failure must never retry,
    or a real bug (bad solve, verify rejection, max_turns) would be
    silently re-run under different auth instead of surfacing."""

    def _run(self, allowed_tools: str = "") -> None:
        run_stage(
            stage="solve",
            schema_filename="generation/solve.gen-schema.json",
            model="claude-sonnet-5",
            max_turns=8,
            allowed_tools=allowed_tools,
            prompt_version="v1",
            problem_json="{}",
        )

    def test_low_credit_failure_retries_once_without_the_api_key(self):
        with (
            mock.patch.dict(
                "os.environ",
                {"ANTHROPIC_API_KEY": "sk-ant-broke", "CLAUDE_CODE_OAUTH_TOKEN": "oauth-tok"},
            ),
            mock.patch(
                "subprocess.run",
                side_effect=[
                    _completed(_LOW_CREDIT_STDOUT, returncode=1),
                    _completed(_OK_STDOUT, returncode=0),
                ],
            ) as mock_run,
        ):
            self._run()

        self.assertEqual(mock_run.call_count, 2)
        first_call, second_call = mock_run.call_args_list
        first_env = first_call.kwargs["env"]
        second_env = second_call.kwargs["env"]
        self.assertIsNone(first_env)
        self.assertNotIn("ANTHROPIC_API_KEY", second_env)
        self.assertEqual(second_env.get("CLAUDE_CODE_OAUTH_TOKEN"), "oauth-tok")

        # --bare forces ANTHROPIC_API_KEY/apiKeyHelper auth and never reads
        # OAuth (confirmed via `claude -p --help`) -- the fallback call must
        # drop it, or CLAUDE_CODE_OAUTH_TOKEN would be silently ignored and
        # the retry would fail the exact same way as the first attempt.
        self.assertIn("--bare", first_call.args[0])
        self.assertNotIn("--bare", second_call.args[0])

    def test_low_credit_failure_without_oauth_token_never_retries(self):
        with (
            mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-broke"}, clear=False),
            mock.patch(
                "subprocess.run", return_value=_completed(_LOW_CREDIT_STDOUT, returncode=1)
            ) as mock_run,
            self.assertRaises(ClaudeStageError),
        ):
            self._run()

        self.assertEqual(mock_run.call_count, 1)

    def test_non_billing_failure_never_retries_even_with_oauth_token_set(self):
        with (
            mock.patch.dict(
                "os.environ",
                {"ANTHROPIC_API_KEY": "sk-ant-broke", "CLAUDE_CODE_OAUTH_TOKEN": "oauth-tok"},
            ),
            mock.patch(
                "subprocess.run", return_value=_completed(_MAX_TURNS_STDOUT, returncode=1)
            ) as mock_run,
            self.assertRaises(ClaudeStageError),
        ):
            self._run()

        self.assertEqual(mock_run.call_count, 1)

    def test_fallback_attempt_also_failing_raises_from_the_fallback(self):
        with (
            mock.patch.dict(
                "os.environ",
                {"ANTHROPIC_API_KEY": "sk-ant-broke", "CLAUDE_CODE_OAUTH_TOKEN": "oauth-tok"},
            ),
            mock.patch(
                "subprocess.run",
                return_value=_completed(_LOW_CREDIT_STDOUT, returncode=1),
            ) as mock_run,
            self.assertRaises(ClaudeStageError) as ctx,
        ):
            self._run()

        self.assertEqual(mock_run.call_count, 2)
        self.assertIn("Credit balance is too low", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
