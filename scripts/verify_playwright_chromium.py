#!/usr/bin/env python3
"""CI-only sanity check, run right after `playwright install chromium` in
.github/workflows/ci.yml: confirms the exact browser
src/rendering/render.py's launch(channel="chromium") needs is actually
present and can launch.

Why this exists: two consecutive CI runs failed 10+ tests deep in pytest
with `RendererNotInstalledError`, even though the "Install Playwright's
Chromium" step reported success both times -- first for the separate
chromium-headless-shell binary, then (after render.py started pinning
channel="chromium" to stop needing that binary) for the regular chromium
build itself. `playwright install` has a documented failure mode where it
exits 0 without leaving a working browser behind (silent download
corruption in CI -- see microsoft/playwright#36412). Rather than let that
surface as a wall of unrelated-looking pytest failures, this fails fast in
its own CI step with a clear message, and retries once via a forced
reinstall before giving up for good.
"""

from __future__ import annotations

import subprocess
import sys


def _try_launch() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="chromium")
            browser.close()
        return True
    except Exception as exc:  # noqa: BLE001 -- any failure here just means "not ready"
        print(f"Chromium launch check failed: {exc}", file=sys.stderr)
        return False


def main() -> int:
    if _try_launch():
        print("Chromium launched successfully.")
        return 0

    print(
        "Retrying with a forced reinstall (playwright install --force chromium)...",
        file=sys.stderr,
    )
    subprocess.run(["playwright", "install", "--force", "--with-deps", "chromium"], check=True)

    if _try_launch():
        print("Chromium launched successfully after reinstall.")
        return 0

    print(
        "Chromium still won't launch after a forced reinstall -- this looks like a "
        "genuine environment/Playwright issue, not a one-off flaky download. See "
        "https://github.com/microsoft/playwright/issues/36412 for the known upstream "
        "download-corruption pattern this guards against.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
