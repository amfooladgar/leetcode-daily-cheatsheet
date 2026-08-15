"""Durable image storage for the public gallery site (scripts/build_gallery.py).

`output/<date>/<problem>/` is disposable (gitignored, regenerated every
run -- see manifest.py's docstring), so it cannot be what a statically
built, publicly hosted site reads from. `gallery/images/` is a small,
committed exception to that rule, holding one copy per successfully
published entry -- see ARCHITECTURE.md "Gallery site" for why this was
chosen over fetching from Google Drive at build time.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

log = logging.getLogger(__name__)


def gallery_image_filename(filename_stem: str, image_path: Path) -> str:
    """The name a rendered image is stored under in gallery/images/ --
    reuses output.filename_pattern (already unique per date+problem) rather
    than the provider-specific `cheatsheet.png` / `cheatsheet-openai-final.png`
    stage filename, so images from either renderer never collide."""
    return f"{filename_stem}{image_path.suffix}"


def save_gallery_image(image_path: Path, gallery_images_dir: Path, filename_stem: str) -> Path:
    """Copies a QA-passed cheat sheet image into the committed gallery
    directory. Call only for a manifest entry that will be recorded with
    status="success" and drive=True (mirrors Manifest.already_published's
    own definition of "published") -- never for a --dry-run or
    --skip-drive invocation."""
    gallery_images_dir.mkdir(parents=True, exist_ok=True)
    dest = gallery_images_dir / gallery_image_filename(filename_stem, image_path)
    shutil.copy2(image_path, dest)
    log.info("Saved gallery image %s", dest)
    return dest
