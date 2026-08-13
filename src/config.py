"""Loads config/settings.yaml. Never loads secrets — those are read directly
from environment variables at the point of use (src/storage/google_drive.py,
src/claude/runner.py via the `claude` CLI's own ANTHROPIC_API_KEY handling),
per CLAUDE.md's "Never hard-code credentials" rule.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SETTINGS_PATH = REPO_ROOT / "config" / "settings.yaml"


def load_settings(path: Path = DEFAULT_SETTINGS_PATH) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"No settings file at {path}")
    with path.open() as f:
        settings = yaml.safe_load(f)
    return settings
