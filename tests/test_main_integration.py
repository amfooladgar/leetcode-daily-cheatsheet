"""End-to-end pipeline test with every external service mocked (LeetCode,
Claude, Google Drive) — exercises the real wiring in src/main.py (argument
parsing, stage sequencing, schema validation, manifest writes) without any
network access or API keys, matching CLAUDE.md's testing rule."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.claude.runner import ClaudeResult
from src.config import load_settings
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

    def test_dry_run_alone_bypasses_the_schedule_gate(self):
        # Regression: docs/SETUP.md step 6 tells users to verify the
        # pipeline is wired correctly via Actions -> "Run workflow" ->
        # dry_run: true, leaving --force and --problem-slug unset (so
        # today's real daily challenge gets fetched) -- "without waiting
        # for tomorrow". But `manual_invocation` didn't check args.dry_run,
        # so that exact flow hit the schedule gate and silently no-op'd
        # (exit 0, zero files) unless it happened to run at exactly
        # schedule.target_hour. --dry-run never uploads to Drive or writes
        # the manifest on its own, so gating it by time of day protects
        # nothing.
        from src.main import main

        with mock.patch("src.main._schedule_gate", return_value=False):
            exit_code = main(["--dry-run"])

        self.assertEqual(exit_code, 0)
        content_path = self.tmpdir / "output" / "2026-08-13" / "1" / "content.json"
        self.assertTrue(
            content_path.exists(),
            "dry-run alone must still run the pipeline, not defer to the schedule gate",
        )

    def test_skip_drive_writes_manifest_with_drive_false(self):
        from src.main import main
        from src.storage.telegram import SendResult

        upload_patch = mock.patch("src.main.upload_cheatsheet")
        mock_upload = upload_patch.start()
        self.addCleanup(upload_patch.stop)

        telegram_patch = mock.patch(
            "src.main.send_cheatsheet",
            return_value=SendResult(message_id=1, chat_id="chat1"),
        )
        telegram_patch.start()
        self.addCleanup(telegram_patch.stop)

        exit_code = main(["--problem-slug", "two-sum", "--skip-drive", "--force"])

        self.assertEqual(exit_code, 0)
        mock_upload.assert_not_called()

        manifest_path = self.tmpdir / "state" / "manifest.json"
        self.assertTrue(manifest_path.exists())
        manifest = json.loads(manifest_path.read_text())
        entry = manifest["2026-08-13:1"]
        self.assertEqual(entry["status"], "success")
        self.assertFalse(entry["drive"])
        self.assertTrue(entry["telegram"])

    def test_second_run_without_force_is_a_noop(self):
        # Idempotency (already_published) requires a *completed Drive
        # upload*, not just a rendered artifact -- a prior --skip-drive or
        # --dry-run run must NOT block a later real publish attempt. So this
        # test's first run uses a mocked, successful Drive upload.
        from src.main import main
        from src.storage.google_drive import UploadResult
        from src.storage.telegram import SendResult

        with mock.patch(
            "src.main.upload_cheatsheet",
            return_value=UploadResult(image_file_id="f1", image_web_link="https://x"),
        ), mock.patch(
            "src.main.send_cheatsheet",
            return_value=SendResult(message_id=1, chat_id="chat1"),
        ), mock.patch.dict("os.environ", {"GOOGLE_DRIVE_FOLDER_ID": "fake-folder-id"}):
            first = main(["--problem-slug", "two-sum", "--force"])
            self.assertEqual(first, 0)

            with mock.patch("src.main.run_stage") as mock_run_stage:
                second = main(["--problem-slug", "two-sum"])
                self.assertEqual(second, 0)
                mock_run_stage.assert_not_called()  # short-circuited by the manifest

    def test_env_file_populates_missing_vars_without_overriding_existing(self):
        # Regression test: main() must load .env (see README.md's "cp
        # .env.example .env" step) so secrets work without the user having
        # to `export` each one by hand -- but a real, already-exported env
        # var (e.g. a GitHub Actions secret in CI) must always win.
        from src.main import main
        from src.storage.google_drive import UploadResult
        from src.storage.telegram import SendResult

        (self.tmpdir / ".env").write_text(
            "GOOGLE_DRIVE_FOLDER_ID=from-dotenv\nSOME_OTHER_VAR=from-dotenv\n"
        )

        with mock.patch(
            "src.main.upload_cheatsheet",
            return_value=UploadResult(image_file_id="f1", image_web_link="https://x"),
        ) as mock_upload, mock.patch(
            "src.main.send_cheatsheet",
            return_value=SendResult(message_id=1, chat_id="chat1"),
        ), mock.patch.dict("os.environ", {"SOME_OTHER_VAR": "already-set"}):
            os.environ.pop("GOOGLE_DRIVE_FOLDER_ID", None)
            exit_code = main(["--problem-slug", "two-sum", "--force"])

            self.assertEqual(exit_code, 0)
            mock_upload.assert_called_once()
            self.assertEqual(mock_upload.call_args.kwargs["root_folder_id"], "from-dotenv")
            self.assertEqual(os.environ["SOME_OTHER_VAR"], "already-set")  # .env did not override

    def test_telegram_failure_does_not_block_successful_drive_upload(self):
        # Drive and Telegram are independent delivery stages (see
        # ARCHITECTURE.md "Failure policy") -- a Telegram send failure must
        # still let a successful Drive upload land in the manifest as
        # drive=true, while marking telegram=false and exiting non-zero so
        # CI surfaces the problem.
        from src.main import main
        from src.storage.google_drive import UploadResult
        from src.storage.telegram import TelegramSendError

        with mock.patch(
            "src.main.upload_cheatsheet",
            return_value=UploadResult(image_file_id="f1", image_web_link="https://x"),
        ), mock.patch(
            "src.main.send_cheatsheet",
            side_effect=TelegramSendError("TELEGRAM_BOT_TOKEN not set"),
        ), mock.patch.dict("os.environ", {"GOOGLE_DRIVE_FOLDER_ID": "fake-folder-id"}):
            exit_code = main(["--problem-slug", "two-sum", "--force"])

        self.assertEqual(exit_code, 1)  # non-zero so CI surfaces the failure
        manifest_path = self.tmpdir / "state" / "manifest.json"
        entry = json.loads(manifest_path.read_text())["2026-08-13:1"]
        self.assertEqual(entry["status"], "success")
        self.assertTrue(entry["drive"])
        self.assertFalse(entry["telegram"])

    def test_drive_failure_does_not_block_successful_telegram_send(self):
        # The reverse of the above: Drive failing must not prevent Telegram
        # from still sending, and the manifest should record drive=false,
        # telegram=true.
        from src.main import main
        from src.storage.google_drive import DriveUploadError
        from src.storage.telegram import SendResult

        with mock.patch(
            "src.main.upload_cheatsheet",
            side_effect=DriveUploadError("GOOGLE_OAUTH_REFRESH_TOKEN not set"),
        ), mock.patch(
            "src.main.send_cheatsheet",
            return_value=SendResult(message_id=7, chat_id="chat1"),
        ), mock.patch.dict("os.environ", {"GOOGLE_DRIVE_FOLDER_ID": "fake-folder-id"}):
            exit_code = main(["--problem-slug", "two-sum", "--force"])

        self.assertEqual(exit_code, 1)
        manifest_path = self.tmpdir / "state" / "manifest.json"
        entry = json.loads(manifest_path.read_text())["2026-08-13:1"]
        self.assertEqual(entry["status"], "success")
        self.assertFalse(entry["drive"])
        self.assertTrue(entry["telegram"])
        self.assertEqual(entry["telegram_message_id"], 7)

    def test_unknown_image_provider_fails_fast_before_any_stage_work(self):
        # Validated right after load_settings(), before FETCHED -- an
        # unattended run must never spend Anthropic tokens (or reach
        # Drive/Telegram) on a broken image_generation.provider value. See
        # ARCHITECTURE.md "Optional OpenAI image renderer".
        from src.main import main

        with mock.patch.dict("os.environ", {"IMAGE_GENERATION_PROVIDER": "dalle"}):
            exit_code = main(["--problem-slug", "two-sum", "--dry-run"])

        self.assertEqual(exit_code, 1)
        content_path = self.tmpdir / "output" / "2026-08-13" / "1" / "content.json"
        self.assertFalse(content_path.exists())

    def test_existing_remains_the_default_provider(self):
        from src.main import main

        upload_patch = mock.patch("src.main.upload_cheatsheet")
        mock_upload = upload_patch.start()
        self.addCleanup(upload_patch.stop)

        with mock.patch("src.main.render_cheatsheet_with_provider") as mock_render:
            mock_render.return_value = mock.MagicMock(
                passed=True,
                image_path=self.tmpdir / "output" / "2026-08-13" / "1" / "cheatsheet.png",
                width=1080,
                height=1350,
                format="PNG",
                provider="existing",
                warnings=[],
                dropped_for_overflow=[],
                failed_checks=[],
            )
            (self.tmpdir / "output" / "2026-08-13" / "1").mkdir(parents=True, exist_ok=True)
            (self.tmpdir / "output" / "2026-08-13" / "1" / "cheatsheet.png").write_bytes(b"fake-png")
            main(["--problem-slug", "two-sum", "--dry-run"])

        self.assertEqual(mock_render.call_args[0][0], "existing")
        mock_upload.assert_not_called()

    def test_openai_provider_success_records_openai_filename_in_manifest(self):
        from src.main import main
        from src.storage.google_drive import UploadResult
        from src.storage.telegram import SendResult

        stage_dir = self.tmpdir / "output" / "2026-08-13" / "1"
        stage_dir.mkdir(parents=True, exist_ok=True)
        final_path = stage_dir / "cheatsheet-openai-final.png"
        final_path.write_bytes(b"fake-final-png")

        with mock.patch(
            "src.main.render_cheatsheet_with_provider",
            return_value=mock.MagicMock(
                passed=True,
                image_path=final_path,
                width=1536,
                height=1024,
                format="PNG",
                provider="openai",
                warnings=[],
                dropped_for_overflow=[],
                failed_checks=[],
            ),
        ) as mock_render, mock.patch(
            "src.main.upload_cheatsheet",
            return_value=UploadResult(image_file_id="f1", image_web_link="https://x"),
        ) as mock_upload, mock.patch(
            "src.main.send_cheatsheet", return_value=SendResult(message_id=1, chat_id="chat1")
        ), mock.patch.dict(
            "os.environ", {"GOOGLE_DRIVE_FOLDER_ID": "fake-folder-id", "OPENAI_API_KEY": "sk-test"}
        ):
            exit_code = main(["--problem-slug", "two-sum", "--image-provider", "openai", "--force"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(mock_render.call_args[0][0], "openai")
        self.assertEqual(mock_upload.call_args.kwargs["image_path"], final_path)

        manifest_path = self.tmpdir / "state" / "manifest.json"
        entry = json.loads(manifest_path.read_text())["2026-08-13:1"]
        self.assertEqual(entry["status"], "success")
        self.assertEqual(entry["image_filename"], "cheatsheet-openai-final.png")

    def test_openai_failure_without_fallback_records_failure_and_never_uploads(self):
        from src.main import main
        from src.rendering.openai_provider import OpenAIGenerationError

        with mock.patch(
            "src.main.render_cheatsheet_with_provider",
            side_effect=OpenAIGenerationError("simulated API failure"),
        ), mock.patch("src.main.upload_cheatsheet") as mock_upload, mock.patch(
            "src.main.send_cheatsheet"
        ) as mock_send, mock.patch.dict(
            "os.environ", {"GOOGLE_DRIVE_FOLDER_ID": "fake-folder-id", "OPENAI_API_KEY": "sk-test"}
        ):
            exit_code = main(["--problem-slug", "two-sum", "--image-provider", "openai", "--force"])

        self.assertEqual(exit_code, 1)
        mock_upload.assert_not_called()
        mock_send.assert_not_called()

        manifest_path = self.tmpdir / "state" / "manifest.json"
        entry = json.loads(manifest_path.read_text())["2026-08-13:1"]
        self.assertEqual(entry["status"], "failed")
        self.assertEqual(entry["failure_stage"], "render_openai")

    def test_telegram_disabled_in_settings_is_a_noop_not_a_failure(self):
        from src.main import main
        from src.storage.google_drive import UploadResult

        with mock.patch(
            "src.main.upload_cheatsheet",
            return_value=UploadResult(image_file_id="f1", image_web_link="https://x"),
        ), mock.patch("src.main.send_cheatsheet") as mock_send, mock.patch.dict(
            "os.environ", {"GOOGLE_DRIVE_FOLDER_ID": "fake-folder-id"}
        ):
            settings = load_settings()
            settings["telegram"]["enabled"] = False
            with mock.patch("src.main.load_settings", return_value=settings):
                exit_code = main(["--problem-slug", "two-sum", "--force"])

        self.assertEqual(exit_code, 0)
        mock_send.assert_not_called()
        manifest_path = self.tmpdir / "state" / "manifest.json"
        entry = json.loads(manifest_path.read_text())["2026-08-13:1"]
        self.assertTrue(entry["drive"])
        self.assertFalse(entry["telegram"])


if __name__ == "__main__":
    unittest.main()
