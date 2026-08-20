"""Telegram delivery via the Bot API (see docs/SETUP.md step 3b and
ARCHITECTURE.md "Failure policy" for how a Telegram failure is treated as
independent of, and non-blocking for, the Drive upload).

No OAuth, no folder hierarchy, no service account -- the bot token is a
plain bearer credential and a single `POST .../sendPhoto` multipart request
delivers the rendered PNG with a caption (headline + LeetCode link) straight
to a chat or channel. See src/storage/google_drive.py for the module this
one is intentionally shaped to match.

One bot, two destinations: `send_cheatsheet()` broadcasts to
TELEGRAM_CHAT_ID (the public channel or DM subscribers see), while
send_message()/send_linkedin_prompt()/get_update_offset()/
await_button_decision() -- the LinkedIn Path A approval flow, see
ARCHITECTURE.md "LinkedIn posting" -- talk to TELEGRAM_OWNER_CHAT_ID (your
own DM with the bot) instead. Keeping these separate means "approve this
LinkedIn post?" prompts and their inline buttons never leak into a channel
full of subscribers.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org"
_SEND_TIMEOUT_SECONDS = 30

# Telegram truncates (and in some clients rejects) captions longer than
# this -- see https://core.telegram.org/bots/api#sendphoto ("caption",
# 0-1024 characters).
_CAPTION_MAX_CHARS = 1024

_ENV_VARS = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
_OWNER_ENV_VARS = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_OWNER_CHAT_ID")


class TelegramSendError(RuntimeError):
    """Raised for any Telegram failure the caller should treat as fatal for
    this run's Telegram stage (see ARCHITECTURE.md 'Failure policy' -- a
    Telegram failure marks the manifest entry telegram=false but does not
    discard the already-rendered artifact and does not block a successful
    Drive upload, matching DriveUploadError's philosophy for the reverse
    case)."""


@dataclass
class SendResult:
    message_id: int
    chat_id: str


def _load_credentials() -> tuple[str, str]:
    values = {name: os.environ.get(name) for name in _ENV_VARS}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise TelegramSendError(
            f"{', '.join(missing)} not set. See docs/SETUP.md step 3b -- "
            "message @BotFather on Telegram to create a bot and get "
            "TELEGRAM_BOT_TOKEN, then message the bot (or add it to a "
            "channel) and hit https://api.telegram.org/bot<TOKEN>/getUpdates "
            "to read back TELEGRAM_CHAT_ID."
        )
    return values["TELEGRAM_BOT_TOKEN"], values["TELEGRAM_CHAT_ID"]


def _load_owner_credentials() -> tuple[str, str]:
    """Like _load_credentials(), but for TELEGRAM_OWNER_CHAT_ID -- the
    owner's own DM with the bot, used for the LinkedIn Path A approval
    flow so those prompts never post to the (possibly public)
    TELEGRAM_CHAT_ID channel. See the module docstring."""
    values = {name: os.environ.get(name) for name in _OWNER_ENV_VARS}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise TelegramSendError(
            f"{', '.join(missing)} not set. See docs/SETUP.md step 3b -- "
            "message @BotFather on Telegram to create a bot and get "
            "TELEGRAM_BOT_TOKEN, then send the bot a DM and hit "
            "https://api.telegram.org/bot<TOKEN>/getUpdates to read back "
            "TELEGRAM_OWNER_CHAT_ID (kept separate from TELEGRAM_CHAT_ID so "
            "owner-only prompts never post to a public channel)."
        )
    return values["TELEGRAM_BOT_TOKEN"], values["TELEGRAM_OWNER_CHAT_ID"]


def _parse_result(response, method: str):
    """Shared response handling for every Bot API call below (send_cheatsheet
    keeps its own inline copy of this logic to avoid touching working code —
    see that function). Returns payload["result"]; raises TelegramSendError
    on any non-JSON body, non-ok status, or ok:false payload."""
    try:
        payload = response.json()
    except ValueError as exc:
        raise TelegramSendError(
            f"{method} returned a non-JSON response (status {response.status_code}): "
            f"{response.text[:500]}"
        ) from exc

    if not response.ok or not payload.get("ok"):
        description = payload.get("description", response.text[:500])
        raise TelegramSendError(f"{method} failed (status {response.status_code}): {description}")

    return payload["result"]


def _truncate_caption(caption: str) -> str:
    if len(caption) <= _CAPTION_MAX_CHARS:
        return caption
    ellipsis = "..."
    return caption[: _CAPTION_MAX_CHARS - len(ellipsis)] + ellipsis


def send_cheatsheet(*, image_path: Path, caption: str) -> SendResult:
    """Sends `image_path` as a photo to the configured chat, with `caption`
    (clamped to Telegram's 1024-char cap). Idempotency (not re-sending the
    same date/problem twice) is enforced by the caller via
    state/manifest.json, not by this module -- this function always sends
    when called, matching upload_cheatsheet()'s contract."""

    # Imported lazily so `python -m src.main --dry-run` and the test suite
    # never require `requests` to be installed just to exercise stages that
    # don't touch Telegram -- though in practice `requests` is already a
    # hard dependency for the LeetCode adapter, so this is mostly for
    # symmetry with google_drive.py's lazy-import pattern.
    import requests

    bot_token, chat_id = _load_credentials()
    url = f"{_API_BASE}/bot{bot_token}/sendPhoto"

    with image_path.open("rb") as image_file:
        try:
            response = requests.post(
                url,
                data={"chat_id": chat_id, "caption": _truncate_caption(caption)},
                files={"photo": (image_path.name, image_file, "image/png")},
                timeout=_SEND_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise TelegramSendError(f"sendPhoto request failed: {exc}") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise TelegramSendError(
            f"sendPhoto returned a non-JSON response (status {response.status_code}): "
            f"{response.text[:500]}"
        ) from exc

    if not response.ok or not payload.get("ok"):
        description = payload.get("description", response.text[:500])
        raise TelegramSendError(f"sendPhoto failed (status {response.status_code}): {description}")

    result = payload["result"]
    log.info(
        "Sent %s to Telegram chat %s (message_id=%s)",
        image_path.name,
        chat_id,
        result["message_id"],
    )
    return SendResult(message_id=result["message_id"], chat_id=chat_id)


def send_message(*, text: str, reply_to_message_id: int | None = None) -> SendResult:
    """Sends a plain text message to the owner's chat (TELEGRAM_OWNER_CHAT_ID)
    -- used by the LinkedIn Path A flow (see src/main.py) to post the drafted
    caption and later confirmations. Deliberately not the broadcast
    TELEGRAM_CHAT_ID: these are owner-only actionable messages, not content
    for channel subscribers (see module docstring). `reply_to_message_id`
    must therefore reference another message already sent to this same
    owner chat, never the broadcast send_cheatsheet() message."""
    import requests

    bot_token, chat_id = _load_owner_credentials()
    url = f"{_API_BASE}/bot{bot_token}/sendMessage"
    data = {"chat_id": chat_id, "text": text}
    if reply_to_message_id is not None:
        data["reply_to_message_id"] = reply_to_message_id

    try:
        response = requests.post(url, data=data, timeout=_SEND_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise TelegramSendError(f"sendMessage request failed: {exc}") from exc

    result = _parse_result(response, "sendMessage")
    return SendResult(message_id=result["message_id"], chat_id=chat_id)


def send_linkedin_prompt(
    *,
    text: str,
    date: str,
    problem_number: int,
    reply_to_message_id: int | None = None,
) -> SendResult:
    """Sends `text` with a "Post now" / "Later" inline keyboard to the
    owner's chat (TELEGRAM_OWNER_CHAT_ID), encoding `date`/`problem_number`
    into each button's callback_data so await_button_decision() can match
    the tap back to this specific run and ignore stale taps from a
    previous day.

    Note: Telegram channels can make inline-button taps behave oddly under
    anonymous-admin mode (the tap's callback_query can arrive without a
    resolvable chat/user in some client versions). TELEGRAM_OWNER_CHAT_ID
    is meant to be your own DM with the bot specifically to avoid this --
    if you point it at a channel or group instead, the same caveat applies.
    """
    import requests

    bot_token, chat_id = _load_owner_credentials()
    url = f"{_API_BASE}/bot{bot_token}/sendMessage"
    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "Post now", "callback_data": f"linkedin_now:{date}:{problem_number}"},
                {"text": "Later", "callback_data": f"linkedin_later:{date}:{problem_number}"},
            ]
        ]
    }
    data = {"chat_id": chat_id, "text": text, "reply_markup": json.dumps(reply_markup)}
    if reply_to_message_id is not None:
        data["reply_to_message_id"] = reply_to_message_id

    try:
        response = requests.post(url, data=data, timeout=_SEND_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise TelegramSendError(f"sendMessage (linkedin prompt) request failed: {exc}") from exc

    result = _parse_result(response, "sendMessage")
    return SendResult(message_id=result["message_id"], chat_id=chat_id)


def get_update_offset() -> int:
    """Returns the current highest update_id seen by this bot (0 if none),
    so a subsequent await_button_decision() only reacts to taps that happen
    after this point. Call this BEFORE send_linkedin_prompt(). Uses
    TELEGRAM_OWNER_CHAT_ID's credentials (bot token only, here) so a
    misconfigured owner chat fails with the right error before any
    Telegram calls happen, rather than surfacing later."""
    import requests

    bot_token, _ = _load_owner_credentials()
    url = f"{_API_BASE}/bot{bot_token}/getUpdates"
    try:
        response = requests.get(
            url, params={"limit": 1, "offset": -1}, timeout=_SEND_TIMEOUT_SECONDS
        )
    except requests.RequestException as exc:
        raise TelegramSendError(f"getUpdates request failed: {exc}") from exc

    updates = _parse_result(response, "getUpdates")
    if not updates:
        return 0
    return updates[-1]["update_id"]


def _answer_callback_query(bot_token: str, callback_query_id: str) -> None:
    import requests

    url = f"{_API_BASE}/bot{bot_token}/answerCallbackQuery"
    try:
        response = requests.post(
            url, data={"callback_query_id": callback_query_id}, timeout=_SEND_TIMEOUT_SECONDS
        )
    except requests.RequestException as exc:
        raise TelegramSendError(f"answerCallbackQuery request failed: {exc}") from exc
    _parse_result(response, "answerCallbackQuery")


def _clear_keyboard(bot_token: str, chat_id: str, message_id: int) -> None:
    import requests

    url = f"{_API_BASE}/bot{bot_token}/editMessageReplyMarkup"
    data = {
        "chat_id": chat_id,
        "message_id": message_id,
        "reply_markup": json.dumps({"inline_keyboard": []}),
    }
    try:
        response = requests.post(url, data=data, timeout=_SEND_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise TelegramSendError(f"editMessageReplyMarkup request failed: {exc}") from exc
    _parse_result(response, "editMessageReplyMarkup")


def await_button_decision(
    *,
    since_update_id: int,
    date: str,
    problem_number: int,
    timeout_seconds: int,
    poll_interval_seconds: int,
    prompt_message_id: int,
) -> str | None:
    """Long-polls Telegram's getUpdates for the "Post now"/"Later" tap on
    the keyboard send_linkedin_prompt() posted for this exact date/problem,
    ignoring callback taps for any other date/problem (a stale button from a
    previous, unanswered run). Returns "now" or "later" on a match, or None
    if `timeout_seconds` elapses with no match.

    `prompt_message_id` (the message_id send_linkedin_prompt() returned) is
    needed so the keyboard can be cleared on a timeout too -- Telegram's
    getUpdates response only carries a message_id when a tap actually
    occurs, so the caller must supply it directly for the no-tap case.
    Either way (matched or timed out), the keyboard on that message is
    cleared via editMessageReplyMarkup before returning, so stale buttons
    from an already-answered or already-expired run can't be tapped again.
    """
    bot_token, configured_chat_id = _load_owner_credentials()

    import requests

    url = f"{_API_BASE}/bot{bot_token}/getUpdates"
    now_data = f"linkedin_now:{date}:{problem_number}"
    later_data = f"linkedin_later:{date}:{problem_number}"

    offset = since_update_id + 1
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        poll_timeout = max(0, min(poll_interval_seconds, int(remaining)))
        try:
            response = requests.get(
                url,
                params={"offset": offset, "timeout": poll_timeout},
                timeout=_SEND_TIMEOUT_SECONDS + poll_timeout,
            )
        except requests.RequestException as exc:
            raise TelegramSendError(f"getUpdates request failed: {exc}") from exc
        updates = _parse_result(response, "getUpdates")

        for update in updates:
            offset = max(offset, update["update_id"] + 1)
            callback = update.get("callback_query")
            if not callback:
                continue
            callback_chat_id = str(callback.get("message", {}).get("chat", {}).get("id", ""))
            if callback_chat_id != str(configured_chat_id):
                continue
            data = callback.get("data")
            if data not in (now_data, later_data):
                continue

            _answer_callback_query(bot_token, callback["id"])
            _clear_keyboard(bot_token, callback_chat_id, callback["message"]["message_id"])
            return "now" if data == now_data else "later"

    _clear_keyboard(bot_token, configured_chat_id, prompt_message_id)
    return None
