"""Tests for scripts/post_to_linkedin.py -- primarily its linkedin.enabled
guard, the second independent check beyond .claude/commands/post-linkedin.md
(see docs/SETUP.md step 3c and ARCHITECTURE.md "LinkedIn posting")."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.post_to_linkedin import main


def _settings(enabled: bool) -> dict:
    return {
        "linkedin": {"enabled": enabled, "visibility": "PUBLIC", "api_version": "202608"},
        "state": {"manifest_path": "state/manifest.json"},
    }


class PostToLinkedInGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.image = self.tmpdir / "cheatsheet.png"
        self.image.write_bytes(b"\x89PNG\r\n\x1a\nfake")
        self.caption_file = self.tmpdir / "caption.txt"
        self.caption_file.write_text("Never Forget This Trick")

    def test_disabled_guard_exits_1_and_never_posts(self):
        with (
            mock.patch("scripts.post_to_linkedin.load_settings", return_value=_settings(False)),
            mock.patch("scripts.post_to_linkedin.post_cheatsheet") as mock_post,
        ):
            exit_code = main(
                [
                    "--image",
                    str(self.image),
                    "--caption-file",
                    str(self.caption_file),
                    "--date",
                    "2026-08-14",
                    "--problem-number",
                    "1",
                ]
            )

        self.assertEqual(exit_code, 1)
        mock_post.assert_not_called()

    def test_enabled_calls_post_cheatsheet(self):
        from src.storage.linkedin import PostResult

        with (
            mock.patch("scripts.post_to_linkedin.REPO_ROOT", self.tmpdir),
            mock.patch("scripts.post_to_linkedin.load_settings", return_value=_settings(True)),
            mock.patch(
                "scripts.post_to_linkedin.post_cheatsheet",
                return_value=PostResult(
                    post_urn="urn:li:share:1",
                    post_url="https://www.linkedin.com/feed/update/urn:li:share:1/",
                ),
            ) as mock_post,
        ):
            exit_code = main(
                [
                    "--image",
                    str(self.image),
                    "--caption-file",
                    str(self.caption_file),
                    "--date",
                    "2026-08-14",
                    "--problem-number",
                    "1",
                ]
            )

        # No manifest entry exists for this date/problem in a fresh tmp
        # manifest -- the script must report that clearly and exit 1 rather
        # than fabricate one, even though the post itself succeeded.
        self.assertEqual(exit_code, 1)
        mock_post.assert_called_once()


if __name__ == "__main__":
    unittest.main()
