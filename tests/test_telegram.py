"""Unit tests for src/storage/telegram.py, following google_drive.py's
mocked-HTTP testing pattern (unittest.mock.patch on the HTTP call, no real
network -- see CLAUDE.md's testing rule)."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.storage.telegram import TelegramSendError, send_cheatsheet


def _fake_png(tmp: Path) -> Path:
    path = tmp / "cheatsheet.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\nfake-image-bytes")
    return path


class TelegramSendTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.addCleanup(mock.patch.stopall)
        env_patch = mock.patch.dict(
            "os.environ",
            {"TELEGRAM_BOT_TOKEN": "123456:fake-token", "TELEGRAM_CHAT_ID": "-1001234567890"},
        )
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def test_missing_credentials_raises(self):
        with mock.patch.dict("os.environ", {}, clear=True), self.assertRaises(TelegramSendError) as ctx:
            send_cheatsheet(image_path=_fake_png(self.tmpdir), caption="hi")
        self.assertIn("TELEGRAM_BOT_TOKEN", str(ctx.exception))
        self.assertIn("TELEGRAM_CHAT_ID", str(ctx.exception))

    def test_successful_send_returns_result(self):
        image_path = _fake_png(self.tmpdir)
        fake_response = mock.Mock(ok=True, status_code=200)
        fake_response.json.return_value = {"ok": True, "result": {"message_id": 42}}

        with mock.patch("requests.post", return_value=fake_response) as mock_post:
            result = send_cheatsheet(image_path=image_path, caption="Never Forget #1 Two Sum")

        self.assertEqual(result.message_id, 42)
        self.assertEqual(result.chat_id, "-1001234567890")

        # sendPhoto is a multipart POST with the bot token in the URL path
        # (not a query param/header) and chat_id + caption as form fields.
        called_url = mock_post.call_args.args[0]
        self.assertIn("bot123456:fake-token/sendPhoto", called_url)
        self.assertEqual(mock_post.call_args.kwargs["data"]["chat_id"], "-1001234567890")
        self.assertEqual(mock_post.call_args.kwargs["data"]["caption"], "Never Forget #1 Two Sum")
        self.assertIn("photo", mock_post.call_args.kwargs["files"])

    def test_telegram_api_error_raises(self):
        fake_response = mock.Mock(ok=False, status_code=400, text='{"ok": false}')
        fake_response.json.return_value = {"ok": False, "description": "chat not found"}

        with mock.patch("requests.post", return_value=fake_response), self.assertRaises(TelegramSendError) as ctx:
            send_cheatsheet(image_path=_fake_png(self.tmpdir), caption="hi")
        self.assertIn("chat not found", str(ctx.exception))

    def test_network_failure_raises(self):
        import requests

        with mock.patch(
            "requests.post", side_effect=requests.ConnectionError("boom")
        ), self.assertRaises(TelegramSendError) as ctx:
            send_cheatsheet(image_path=_fake_png(self.tmpdir), caption="hi")
        self.assertIn("boom", str(ctx.exception))

    def test_non_json_response_raises(self):
        fake_response = mock.Mock(ok=True, status_code=200, text="<html>not json</html>")
        fake_response.json.side_effect = ValueError("no JSON object could be decoded")

        with mock.patch("requests.post", return_value=fake_response), self.assertRaises(TelegramSendError):
            send_cheatsheet(image_path=_fake_png(self.tmpdir), caption="hi")

    def test_caption_is_truncated_to_1024_chars(self):
        long_caption = "x" * 2000
        fake_response = mock.Mock(ok=True, status_code=200)
        fake_response.json.return_value = {"ok": True, "result": {"message_id": 1}}

        with mock.patch("requests.post", return_value=fake_response) as mock_post:
            send_cheatsheet(image_path=_fake_png(self.tmpdir), caption=long_caption)

        sent_caption = mock_post.call_args.kwargs["data"]["caption"]
        self.assertEqual(len(sent_caption), 1024)
        self.assertTrue(sent_caption.endswith("..."))


if __name__ == "__main__":
    unittest.main()
