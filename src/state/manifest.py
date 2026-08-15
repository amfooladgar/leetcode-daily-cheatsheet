"""Idempotency ledger: state/manifest.json.

Keyed by "<date>:<problem_number>" so a run is a well-defined no-op unless
--force is passed (see ARCHITECTURE.md "Every run must be idempotent" and
docs/OPERATIONS.md "Re-running a specific day"). The manifest is the only
piece of state the pipeline persists between runs — everything else in
output/<date>/<problem>/ is disposable debugging output.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class ManifestEntry:
    date: str
    problem_number: int
    slug: str
    status: str  # "success" | "failed"
    content_hash: str
    image_filename: str | None = None
    drive: bool = False
    drive_file_id: str | None = None
    telegram: bool = False
    telegram_message_id: int | None = None
    linkedin: bool = False
    linkedin_post_urn: str | None = None
    linkedin_draft_saved: bool = False
    linkedin_draft_drive_file_id: str | None = None
    failure_stage: str | None = None
    failure_reason: str | None = None
    prompt_version: str | None = None
    # Denormalized gallery metadata (scripts/build_gallery.py). Populated
    # only on a successful run -- see manifest.py's own docstring above:
    # this file is "the only piece of state the pipeline persists between
    # runs", so gallery display data lives here rather than in a second
    # committed store, since output/<date>/<problem>/content.json is
    # disposable and gone by the time the gallery is built.
    title: str | None = None
    difficulty: str | None = None
    topics: list[str] = field(default_factory=list)
    headline: str | None = None
    problem_url: str | None = None


@dataclass
class Manifest:
    entries: dict[str, ManifestEntry] = field(default_factory=dict)

    @staticmethod
    def key(date: str, problem_number: int) -> str:
        return f"{date}:{problem_number}"

    def get(self, date: str, problem_number: int) -> ManifestEntry | None:
        return self.entries.get(self.key(date, problem_number))

    def already_published(self, date: str, problem_number: int) -> bool:
        entry = self.get(date, problem_number)
        return entry is not None and entry.status == "success" and entry.drive

    def record(self, entry: ManifestEntry) -> None:
        self.entries[self.key(entry.date, entry.problem_number)] = entry


def content_hash(cheatsheet: dict) -> str:
    """A stable hash of the cheatsheet content, independent of key order —
    useful for OPERATIONS.md debugging (did a --force rerun actually change
    anything?), not itself used to gate idempotency (date+problem_number is)."""
    canonical = json.dumps(cheatsheet, sort_keys=True).encode()
    return hashlib.sha256(canonical).hexdigest()[:16]


def load(path: Path) -> Manifest:
    if not path.exists():
        return Manifest()
    raw = json.loads(path.read_text())
    entries = {k: ManifestEntry(**v) for k, v in raw.items()}
    return Manifest(entries=entries)


def save(manifest: Manifest, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serializable = {k: asdict(v) for k, v in manifest.entries.items()}
    path.write_text(json.dumps(serializable, indent=2, sort_keys=True) + "\n")
    log.info("Wrote %s (%d entries)", path, len(manifest.entries))
