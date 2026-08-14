"""Shared result type for every image-generation provider (src/rendering/
existing_provider.py, src/rendering/openai_provider.py), returned by
src/rendering/factory.py::render_cheatsheet_with_provider() so src/main.py
has one uniform shape to check regardless of which provider ran.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RenderResult:
    provider: str
    passed: bool
    width: int
    height: int
    format: str
    image_path: Path
    checks: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    # Only ever non-empty for provider="existing" -- see existing_provider.py.
    dropped_for_overflow: list = field(default_factory=list)

    @property
    def failed_checks(self) -> list:
        return [name for name, ok in self.checks.items() if not ok]
