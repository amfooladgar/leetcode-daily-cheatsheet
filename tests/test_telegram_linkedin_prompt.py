"""Unit tests for src/storage/telegram.py's LinkedIn Path A helpers
(send_message, send_linkedin_prompt, get_update_offset,
await_button_decision) -- see tests/test_telegram.py for send_cheatsheet's
own tests. Same mocked-HTTP pattern: zero real network, zero real sleeping
(timeouts are kept tiny since requests.get/requests.post are mocked and
return instantly -- the only real wall-clock cost is the while loop's own
time.monotonic() bookkeeping)."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.storage.telegram import (
    TelegramSendError,
    await_button_decision,
    get_update_offset,
    send_linkedin_prompt,
    send_message,
)

_CHAT_ID = "-1001234567890"  # broadcast TELEGRAM_CHAT_ID -- unused by these owner-chat helpers
_OWNER_CHAT_ID = "555555555"  # TELEGRAM_OWNER_CHAT_ID -- what these helpers actually send to


def _ok_response(result) -> mock.Mock:
    resp = mock.Mock(ok=True, status_code=200)
    resp.json.return_value = {"ok": True, "result": result}
    return resp


def _callback_update(
    update_id: int, data: str, chat_id: str = _OWNER_CHAT_ID, message_id: int = 999
) -> dict:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"cbq{update_id}",
            "data": data,
            "message": {"message_id": message_id, "chat": {"id": chat_id}},
        },
    }


class TelegramLinkedInPromptTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.addCleanup(mock.patch.stopall)
        env_patch = mock.patch.dict(
            "os.environ",
            {
                "TELEGRAM_BOT_TOKEN": "123456:fake-token",
                "TELEGRAM_CHAT_ID": _CHAT_ID,
                "TELEGRAM_OWNER_CHAT_ID": _OWNER_CHAT_ID,
            },
        )
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def test_send_message_posts_text(self):
        fake_response = _ok_response({"message_id": 7})
        with mock.patch("requests.post", return_value=fake_response) as mock_post:
            result = send_message(text="hello", reply_to_message_id=3)

        self.assertEqual(result.message_id, 7)
        self.assertEqual(result.chat_id, _OWNER_CHAT_ID)
        self.assertIn("sendMessage", mock_post.call_args.args[0])
        self.assertEqual(mock_post.call_args.kwargs["data"]["text"], "hello")
        self.assertEqual(mock_post.call_args.kwargs["data"]["reply_to_message_id"], 3)
        # Owner-only message -- must go to TELEGRAM_OWNER_CHAT_ID, never the
        # broadcast TELEGRAM_CHAT_ID a channel's subscribers would see.
        self.assertEqual(mock_post.call_args.kwargs["data"]["chat_id"], _OWNER_CHAT_ID)

    def test_send_linkedin_prompt_builds_inline_keyboard(self):
        fake_response = _ok_response({"message_id": 8})
        with mock.patch("requests.post", return_value=fake_response) as mock_post:
            result = send_linkedin_prompt(
                text="Post this to LinkedIn now, or later?",
                date="2026-08-14",
                problem_number=1,
            )

        self.assertEqual(result.message_id, 8)
        sent = mock_post.call_args.kwargs["data"]
        self.assertEqual(sent["chat_id"], _OWNER_CHAT_ID)
        reply_markup = json.loads(sent["reply_markup"])
        buttons = reply_markup["inline_keyboard"][0]
        self.assertEqual(
            buttons[0], {"text": "Post now", "callback_data": "linkedin_now:2026-08-14:1"}
        )
        self.assertEqual(
            buttons[1], {"text": "Later", "callback_data": "linkedin_later:2026-08-14:1"}
        )

    def test_get_update_offset_returns_highest_update_id(self):
        fake_response = _ok_response([{"update_id": 42}])
        with mock.patch("requests.get", return_value=fake_response) as mock_get:
            offset = get_update_offset()
        self.assertEqual(offset, 42)
        self.assertEqual(mock_get.call_args.kwargs["params"], {"limit": 1, "offset": -1})

    def test_get_update_offset_returns_zero_when_no_updates(self):
        with mock.patch("requests.get", return_value=_ok_response([])):
            self.assertEqual(get_update_offset(), 0)

    def test_await_button_decision_matches_now_and_clears_keyboard(self):
        get_response = _ok_response([_callback_update(1, "linkedin_now:2026-08-14:1")])
        post_response = _ok_response({})

        with (
            mock.patch("requests.get", return_value=get_response),
            mock.patch("requests.post", return_value=post_response) as mock_post,
        ):
            decision = await_button_decision(
                since_update_id=0,
                date="2026-08-14",
                problem_number=1,
                timeout_seconds=5,
                poll_interval_seconds=1,
                prompt_message_id=999,
            )

        self.assertEqual(decision, "now")
        called_methods = [call.args[0] for call in mock_post.call_args_list]
        self.assertTrue(any("answerCallbackQuery" in url for url in called_methods))
        edit_calls = [c for c in mock_post.call_args_list if "editMessageReplyMarkup" in c.args[0]]
        self.assertEqual(len(edit_calls), 1)
        self.assertEqual(
            json.loads(edit_calls[0].kwargs["data"]["reply_markup"]), {"inline_keyboard": []}
        )

    def test_await_button_decision_ignores_other_date_problem_and_times_out(self):
        # A stale tap for a different date -- must be ignored, not treated
        # as this run's answer.
        get_response = _ok_response([_callback_update(1, "linkedin_now:2026-08-13:1")])
        post_response = _ok_response({})

        with (
            mock.patch("requests.get", return_value=get_response),
            mock.patch("requests.post", return_value=post_response) as mock_post,
        ):
            decision = await_button_decision(
                since_update_id=0,
                date="2026-08-14",
                problem_number=1,
                timeout_seconds=0.05,
                poll_interval_seconds=0,
                prompt_message_id=999,
            )

        self.assertIsNone(decision)
        # No answerCallbackQuery for the ignored stale tap...
        answer_calls = [c for c in mock_post.call_args_list if "answerCallbackQuery" in c.args[0]]
        self.assertEqual(len(answer_calls), 0)
        # ...but the timeout path still clears the original prompt's keyboard.
        edit_calls = [c for c in mock_post.call_args_list if "editMessageReplyMarkup" in c.args[0]]
        self.assertEqual(len(edit_calls), 1)
        self.assertEqual(edit_calls[0].kwargs["data"]["message_id"], 999)

    def test_await_button_decision_times_out_with_no_updates(self):
        get_response = _ok_response([])
        post_response = _ok_response({})

        with (
            mock.patch("requests.get", return_value=get_response),
            mock.patch("requests.post", return_value=post_response) as mock_post,
        ):
            decision = await_button_decision(
                since_update_id=0,
                date="2026-08-14",
                problem_number=1,
                timeout_seconds=0.05,
                poll_interval_seconds=0,
                prompt_message_id=999,
            )

        self.assertIsNone(decision)
        edit_calls = [c for c in mock_post.call_args_list if "editMessageReplyMarkup" in c.args[0]]
        self.assertEqual(len(edit_calls), 1)

    def test_send_message_network_failure_raises(self):
        import requests

        with (
            mock.patch("requests.post", side_effect=requests.ConnectionError("boom")),
            self.assertRaises(TelegramSendError),
        ):
            send_message(text="hi")

    def test_await_button_decision_ignores_tap_from_broadcast_chat(self):
        # A matching date/problem tap, but posted in the broadcast channel
        # (TELEGRAM_CHAT_ID) rather than the owner's DM (TELEGRAM_OWNER_CHAT_ID)
        # -- must be ignored, since only the owner chat's keyboard is real.
        get_response = _ok_response(
            [_callback_update(1, "linkedin_now:2026-08-14:1", chat_id=_CHAT_ID)]
        )
        post_response = _ok_response({})

        with (
            mock.patch("requests.get", return_value=get_response),
            mock.patch("requests.post", return_value=post_response) as mock_post,
        ):
            decision = await_button_decision(
                since_update_id=0,
                date="2026-08-14",
                problem_number=1,
                timeout_seconds=0.05,
                poll_interval_seconds=0,
                prompt_message_id=999,
            )

        self.assertIsNone(decision)
        answer_calls = [c for c in mock_post.call_args_list if "answerCallbackQuery" in c.args[0]]
        self.assertEqual(len(answer_calls), 0)

    def test_missing_owner_chat_id_raises(self):
        with (
            mock.patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "123456:fake-token"}, clear=True),
            self.assertRaises(TelegramSendError) as ctx,
        ):
            send_message(text="hi")
        self.assertIn("TELEGRAM_OWNER_CHAT_ID", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
