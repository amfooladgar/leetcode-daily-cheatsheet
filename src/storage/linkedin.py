"""LinkedIn delivery via the Posts API (see docs/SETUP.md step 3c and
ARCHITECTURE.md "LinkedIn posting" for the two human-gated entry points that
are allowed to call this module -- src/main.py's Path A, after a Telegram
button tap, and .claude/commands/post-linkedin.md's Path B, after explicit
chat approval).

No OAuth client library -- a LinkedIn access token is a plain bearer
credential and posting an image is a three-call flow (register upload ->
PUT the image bytes -> create the post). See src/storage/telegram.py for
the module this one is intentionally shaped to match.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_API_BASE = "https://api.linkedin.com"
_REQUEST_TIMEOUT_SECONDS = 30

# LinkedIn's post text limit -- see
# https://learn.microsoft.com/en-us/linkedin/marketing/integrations/community-management/shares/posts-api
_COMMENTARY_MAX_CHARS = 3000

_ENV_VARS = ("LINKEDIN_ACCESS_TOKEN", "LINKEDIN_PERSON_URN")


class LinkedInPostError(RuntimeError):
    """Raised for any LinkedIn failure the caller should treat as fatal for
    this run's LinkedIn stage. Unlike TelegramSendError/DriveUploadError,
    a LinkedIn failure never marks a manifest entry as failed on its own --
    both call sites (src/main.py's Path A, scripts/post_to_linkedin.py's
    Path B) catch this and fall back to (or simply report) a non-posting
    outcome instead, since LinkedIn posting is always optional relative to
    the cheat sheet itself being rendered/delivered."""


@dataclass
class PostResult:
    post_urn: str
    post_url: str


def _load_credentials() -> tuple[str, str]:
    values = {name: os.environ.get(name) for name in _ENV_VARS}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise LinkedInPostError(
            f"{', '.join(missing)} not set. See docs/SETUP.md step 3c -- "
            "create a LinkedIn developer app, then run "
            "`python scripts/authorize_linkedin.py` once to obtain an "
            "access token and your person URN."
        )
    return values["LINKEDIN_ACCESS_TOKEN"], values["LINKEDIN_PERSON_URN"]


def _truncate_caption(caption: str) -> str:
    if len(caption) <= _COMMENTARY_MAX_CHARS:
        return caption
    ellipsis = "..."
    return caption[: _COMMENTARY_MAX_CHARS - len(ellipsis)] + ellipsis


def _headers(token: str, api_version: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "LinkedIn-Version": api_version,
        "X-Restli-Protocol-Version": "2.0.0",
        "Content-Type": "application/json",
    }


def post_cheatsheet(
    *,
    image_path: Path,
    caption: str,
    visibility: str = "PUBLIC",
    api_version: str = "202608",
) -> PostResult:
    """Publishes `image_path` with `caption` (clamped to LinkedIn's
    3000-char commentary cap) to the configured person's profile. Always
    posts when called -- idempotency (not posting the same date/problem
    twice) is the caller's responsibility (see state/manifest.json's
    `linkedin`/`linkedin_post_urn` fields), matching
    upload_cheatsheet()/send_cheatsheet()'s contract."""

    # Imported lazily, same rationale as google_drive.py/telegram.py: the
    # test suite and `python -m src.main --dry-run` never require
    # `requests` to be installed just to exercise stages that don't touch
    # LinkedIn.
    import requests

    token, person_urn = _load_credentials()
    headers = _headers(token, api_version)

    # 1. Register the image upload.
    try:
        init_resp = requests.post(
            f"{_API_BASE}/rest/images?action=initializeUpload",
            headers=headers,
            json={"initializeUploadRequest": {"owner": person_urn}},
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise LinkedInPostError(f"initializeUpload request failed: {exc}") from exc

    if init_resp.status_code != 200:
        raise LinkedInPostError(
            f"initializeUpload failed (status {init_resp.status_code}): {init_resp.text[:500]}"
        )
    try:
        init_payload = init_resp.json()
        upload_url = init_payload["value"]["uploadUrl"]
        image_urn = init_payload["value"]["image"]
    except (ValueError, KeyError) as exc:
        raise LinkedInPostError(
            f"initializeUpload returned an unexpected response body: {init_resp.text[:500]}"
        ) from exc

    # 2. Upload the raw image bytes to the pre-signed URL.
    try:
        upload_resp = requests.put(
            upload_url,
            headers={"Authorization": f"Bearer {token}"},
            data=image_path.read_bytes(),
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise LinkedInPostError(f"Image upload request failed: {exc}") from exc

    if not (200 <= upload_resp.status_code < 300):
        raise LinkedInPostError(
            f"Image upload failed (status {upload_resp.status_code}): {upload_resp.text[:500]}"
        )

    # 3. Create the post referencing the uploaded image.
    body = {
        "author": person_urn,
        "commentary": _truncate_caption(caption),
        "visibility": visibility,
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "content": {"media": {"id": image_urn}},
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    try:
        post_resp = requests.post(
            f"{_API_BASE}/rest/posts",
            headers=headers,
            json=body,
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise LinkedInPostError(f"Create-post request failed: {exc}") from exc

    if post_resp.status_code != 201:
        try:
            message = post_resp.json().get("message", post_resp.text[:500])
        except ValueError:
            message = post_resp.text[:500]
        raise LinkedInPostError(f"Create-post failed (status {post_resp.status_code}): {message}")

    post_urn = post_resp.headers.get("x-restli-id")
    if not post_urn:
        raise LinkedInPostError(
            "Create-post returned 201 but no x-restli-id response header was present"
        )
    post_url = f"https://www.linkedin.com/feed/update/{post_urn}/"
    log.info("Published %s to LinkedIn: %s", image_path.name, post_url)
    return PostResult(post_urn=post_urn, post_url=post_url)
