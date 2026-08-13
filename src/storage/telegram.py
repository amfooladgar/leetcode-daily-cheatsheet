"""Telegram delivery via the Bot API (see docs/SETUP.md step 3.5 and
ARCHITECTURE.md "Failure policy" for how a Telegram failure is treated as
independent of, and non-blocking for, the Drive upload).

No OAuth, no folder hierarchy, no service account -- the bot token is a
plain bearer credential and a single `POST .../sendPhoto` multipart request
delivers the rendered PNG with a caption (headline + LeetCode link) straight
to a chat or channel. See src/storage/google_drive.py for the module this
one is intentionally shaped to match.
"""

from __future__ import annotations

import logging
import os
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
            f"{', '.join(missing)} not set. See docs/SETUP.md step 3.5 -- "
            "message @BotFather on Telegram to create a bot and get "
            "TELEGRAM_BOT_TOKEN, then message the bot (or add it to a "
            "channel) and hit https://api.telegram.org/bot<TOKEN>/getUpdates "
            "to read back TELEGRAM_CHAT_ID."
        )
    return values["TELEGRAM_BOT_TOKEN"], values["TELEGRAM_CHAT_ID"]


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
        raise TelegramSendError(
            f"sendPhoto failed (status {response.status_code}): {description}"
        )

    result = payload["result"]
    log.info("Sent %s to Telegram chat %s (message_id=%s)", image_path.name, chat_id, result["message_id"])
    return SendResult(message_id=result["message_id"], chat_id=chat_id)
