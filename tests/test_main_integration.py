"""End-to-end pipeline test with every external service mocked (LeetCode,
Claude, Google Drive) — exercises the real wiring in src/main.py (argument
parsing, stage sequencing, schema validation, manifest writes) without any
network access or API keys, matching CLAUDE.md's testing rule."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.claude.runner import ClaudeResult
from src.leetcode.client import RawQuestion
from tests.helpers import FIXTURES, load_sample_cheatsheet_json


def _fake_run_stage(*, stage, **kwargs):
    if stage == "solve":
        cheatsheet = load_sample_cheatsheet_json()
        solve_payload = {
            "problem": cheatsheet["problem"],
            "key_insight": cheatsheet["key_insight"],
            "intuition": cheatsheet["intuition"],
            "naive_approach": cheatsheet["naive_approach"],
            "approach": cheatsheet["approach"],
            "example": cheatsheet["example"],
            "correctness": cheatsheet["correctness"],
            "complexity": cheatsheet["complexity"],
            "code": cheatsheet["code"],
            "diagrams": cheatsheet.get("diagrams", []),
            "reasoning_panel": cheatsheet.get("reasoning_panel"),
        }
        return ClaudeResult(structured_output=solve_payload, cost_usd=0.01, session_id="s1", raw={})
    if stage == "verify":
        return ClaudeResult(
            structured_output={
                "valid": True,
                "issues": [],
                "corrected_code": None,
                "time_complexity": "O(n)",
                "space_complexity": "O(n)",
            },
            cost_usd=0.01,
            session_id="s2",
            raw={},
        )
    if stage == "compress":
        cheatsheet = load_sample_cheatsheet_json()
        return ClaudeResult(structured_output=cheatsheet, cost_usd=0.01, session_id="s3", raw={})
    raise AssertionError(f"unexpected stage {stage}")


class MainPipelineIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)

        # Redirect REPO_ROOT-relative output/state paths into a temp dir by
        # patching the module constant, so a test run never writes into the
        # real repo's output/ or state/ directories.
        self.repo_root_patch = mock.patch("src.main.REPO_ROOT", self.tmpdir)
        self.repo_root_patch.start()
        self.addCleanup(self.repo_root_patch.stop)

        (self.tmpdir / "output").mkdir()
        (self.tmpdir / "state").mkdir()

        raw = RawQuestion(
            date="2026-08-13",
            frontend_id="1",
            title="Two Sum",
            slug="two-sum",
            difficulty="Easy",
            content_html=(FIXTURES / "sample_problem_content.html").read_text(),
            topics=["Array", "Hash Table"],
            example_testcases="",
            python_template="",
            is_premium=False,
        )
        client_patch = mock.patch("src.main.LeetCodeClient")
        mock_client_cls = client_patch.start()
        self.addCleanup(client_patch.stop)
        mock_client_cls.return_value.fetch_daily.return_value = raw
        mock_client_cls.return_value.fetch_by_slug.return_value = raw

        run_stage_patch = mock.patch("src.main.run_stage", side_effect=_fake_run_stage)
        run_stage_patch.start()
        self.addCleanup(run_stage_patch.stop)

    def test_dry_run_succeeds_and_never_touches_drive(self):
        from src.main import main

        upload_patch = mock.patch("src.main.upload_cheatsheet")
        mock_upload = upload_patch.start()
        self.addCleanup(upload_patch.stop)

        exit_code = main(["--problem-slug", "two-sum", "--dry-run"])

        self.assertEqual(exit_code, 0)
        mock_upload.assert_not_called()

        content_path = self.tmpdir / "output" / "2026-08-13" / "1" / "content.json"
        image_path = self.tmpdir / "output" / "2026-08-13" / "1" / "cheatsheet.png"
        self.assertTrue(content_path.exists())
        self.assertTrue(image_path.exists())
        content = json.loads(content_path.read_text())
        self.assertEqual(content["problem"]["number"], 1)

        # Dry run must not write the manifest.
        self.assertFalse((self.tmpdir / "state" / "manifest.json").exists())

    def test_skip_drive_writes_manifest_with_drive_false(self):
        from src.main import main

        upload_patch = mock.patch("src.main.upload_cheatsheet")
        mock_upload = upload_patch.start()
        self.addCleanup(upload_patch.stop)

        exit_code = main(["--problem-slug", "two-sum", "--skip-drive", "--force"])

        self.assertEqual(exit_code, 0)
        mock_upload.assert_not_called()

        manifest_path = self.tmpdir / "state" / "manifest.json"
        self.assertTrue(manifest_path.exists())
        manifest = json.loads(manifest_path.read_text())
        entry = manifest["2026-08-13:1"]
        self.assertEqual(entry["status"], "success")
        self.assertFalse(entry["drive"])

    def test_second_run_without_force_is_a_noop(self):
        # Idempotency (already_published) requires a *completed Drive
        # upload*, not just a rendered artifact -- a prior --skip-drive or
        # --dry-run run must NOT block a later real publish attempt. So this
        # test's first run uses a mocked, successful Drive upload.
        from src.main import main
        from src.storage.google_drive import UploadResult

        with mock.patch(
            "src.main.upload_cheatsheet",
            return_value=UploadResult(image_file_id="f1", image_web_link="https://x"),
        ), mock.patch.dict("os.environ", {"GOOGLE_DRIVE_FOLDER_ID": "fake-folder-id"}):
            first = main(["--problem-slug", "two-sum", "--force"])
            self.assertEqual(first, 0)

            with mock.patch("src.main.run_stage") as mock_run_stage:
                second = main(["--problem-slug", "two-sum"])
                self.assertEqual(second, 0)
                mock_run_stage.assert_not_called()  # short-circuited by the manifest


if __name__ == "__main__":
    unittest.main()
