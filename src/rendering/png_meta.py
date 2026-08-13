"""Reads width/height straight out of a PNG's IHDR chunk via stdlib struct,
so the renderer's QA gate doesn't need an image library dependency just to
confirm what Playwright already wrote to disk."""

from __future__ import annotations

import struct
from pathlib import Path

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def read_png_size(path: Path) -> tuple[int, int]:
    data = Path(path).read_bytes()[:33]
    if data[:8] != _PNG_SIGNATURE:
        raise ValueError(f"{path} is not a PNG file (bad signature)")
    # IHDR chunk: 4 bytes length, 4 bytes "IHDR", 4 bytes width, 4 bytes height, ...
    width, height = struct.unpack(">II", data[16:24])
    return width, height
