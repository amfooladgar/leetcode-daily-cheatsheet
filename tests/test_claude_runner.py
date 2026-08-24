"""Unit tests for src/claude/runner.py's subprocess invocation and failure
diagnostics -- no network access, no API keys, subprocess.run is mocked."""

import json
import subprocess
import unittest
from unittest import mock

from src.claude.runner import ClaudeStageError, run_stage


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


if __name__ == "__main__":
    unittest.main()
