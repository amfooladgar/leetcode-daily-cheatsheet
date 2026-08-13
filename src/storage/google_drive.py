"""Google Drive upload via a service account (see docs/SETUP.md step 3 for
why a service account rather than interactive OAuth).

Folder layout created/reused under the configured root folder ID:

    <GOOGLE_DRIVE_FOLDER_ID>/
      LeetCode/
        2026/
          08/
            1234-two-sum-2026-08-13.png
            1234-two-sum-2026-08-13.md   (optional, see drive.upload_markdown_summary)
"""

from __future__ import annotations

import io
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]
_FOLDER_MIME = "application/vnd.google-apps.folder"


class DriveUploadError(RuntimeError):
    """Raised for any Drive failure the caller should treat as fatal for
    this run's upload stage (see ARCHITECTURE.md 'Failure policy' — a Drive
    failure marks the manifest entry drive=false but does not discard the
    already-rendered artifact)."""


@dataclass
class UploadResult:
    image_file_id: str
    image_web_link: str
    markdown_file_id: str | None = None


def _load_credentials():
    # Imported lazily so `python -m src.main --dry-run` and the test suite
    # never require google-api-python-client / google-auth to be installed
    # just to exercise stages that don't touch Drive.
    from google.oauth2 import service_account

    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw:
        raise DriveUploadError(
            "GOOGLE_SERVICE_ACCOUNT_JSON is not set. See docs/SETUP.md step 3."
        )

    # Accept either the raw JSON (as GitHub Actions secrets store it) or a
    # local file path (convenient for `.env` during development).
    info = json.loads(raw) if raw.strip().startswith("{") else json.loads(Path(raw).read_text())

    return service_account.Credentials.from_service_account_info(info, scopes=_DRIVE_SCOPES)


def _build_service():
    from googleapiclient.discovery import build

    creds = _load_credentials()
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _find_or_create_folder(service, name: str, parent_id: str) -> str:
    query = (
        f"name = '{name}' and mimeType = '{_FOLDER_MIME}' "
        f"and '{parent_id}' in parents and trashed = false"
    )
    resp = service.files().list(q=query, fields="files(id, name)", pageSize=1).execute()
    files = resp.get("files", [])
    if files:
        return files[0]["id"]

    metadata = {"name": name, "mimeType": _FOLDER_MIME, "parents": [parent_id]}
    created = service.files().create(body=metadata, fields="id").execute()
    log.info("Created Drive folder '%s' under parent %s", name, parent_id)
    return created["id"]


def _upload_bytes(service, name: str, data: bytes, mime_type: str, parent_id: str) -> dict:
    from googleapiclient.http import MediaIoBaseUpload

    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime_type, resumable=False)
    metadata = {"name": name, "parents": [parent_id]}
    return (
        service.files()
        .create(body=metadata, media_body=media, fields="id, webViewLink")
        .execute()
    )


def upload_cheatsheet(
    *,
    image_path: Path,
    markdown_path: Path | None,
    filename_stem: str,
    root_folder_id: str,
    category_folder_name: str,
    organize_by_year_month: bool,
    year: str,
    month: str,
) -> UploadResult:
    """Uploads the rendered PNG (and optional markdown summary) into
    <root_folder_id>/<category_folder_name>/[<year>/<month>/], creating any
    missing subfolders. Idempotency (not re-uploading the same date/problem
    twice) is enforced by the caller via state/manifest.json, not by this
    module — this function always uploads when called."""

    service = _build_service()

    folder_id = _find_or_create_folder(service, category_folder_name, root_folder_id)
    if organize_by_year_month:
        folder_id = _find_or_create_folder(service, year, folder_id)
        folder_id = _find_or_create_folder(service, month, folder_id)

    image_bytes = image_path.read_bytes()
    image_upload = _upload_bytes(
        service, f"{filename_stem}.png", image_bytes, "image/png", folder_id
    )
    log.info("Uploaded %s.png to Drive folder %s", filename_stem, folder_id)

    markdown_file_id = None
    if markdown_path and markdown_path.exists():
        md_upload = _upload_bytes(
            service,
            f"{filename_stem}.md",
            markdown_path.read_bytes(),
            "text/markdown",
            folder_id,
        )
        markdown_file_id = md_upload["id"]

    return UploadResult(
        image_file_id=image_upload["id"],
        image_web_link=image_upload.get("webViewLink", ""),
        markdown_file_id=markdown_file_id,
    )
