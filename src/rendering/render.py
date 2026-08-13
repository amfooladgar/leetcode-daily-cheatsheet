"""HTML/CSS -> PNG cheat-sheet renderer.

Replaces the earlier Pillow-based compositor (see ARCHITECTURE.md "Why the
renderer moved to HTML/CSS"). The page is built once as a fully self-
contained HTML string (fonts and the optional contact-card image embedded
as base64 data URIs, so nothing is fetched over the network at render
time), then screenshotted at the exact target canvas size by headless
Chromium via Playwright.

Public interface is deliberately unchanged from the old compositor so
src/main.py only needs to update its import line:

    render_cheatsheet(cheatsheet: dict, settings: dict, output_path: Path,
                       *, contact_card_path: Path | None) -> QAResult
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from src.rendering.code_highlight import highlight_python_html
from src.rendering.png_meta import read_png_size

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

_MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".ttf": "font/ttf",
}


class RendererNotInstalledError(RuntimeError):
    """Raised when Playwright's Chromium binary hasn't been downloaded yet.
    `pip install -r requirements.txt` installs the `playwright` Python
    package, but the browser binary itself is a separate, one-time
    download -- `pip` has no way to trigger it automatically. See
    docs/SETUP.md / README.md "Quick start"."""


@dataclass
class QAResult:
    passed: bool
    width: int
    height: int
    format: str
    checks: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)

    @property
    def failed_checks(self) -> list:
        return [name for name, ok in self.checks.items() if not ok]


def _data_uri(path: Path) -> str:
    mime = _MIME_BY_SUFFIX.get(path.suffix.lower(), "application/octet-stream")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _build_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html.jinja2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _render_html(cheatsheet: dict, settings: dict, *, contact_card_path: Path | None) -> str:
    design_cfg = settings["design"]
    image_cfg = settings["image"]
    contact_cfg = settings["contact_card"]

    font_heading_path = REPO_ROOT / design_cfg["font_heading"]
    font_body_path = REPO_ROOT / design_cfg["font_body"]
    font_code_path = REPO_ROOT / design_cfg["font_code"]
    font_code_bold_path = REPO_ROOT / design_cfg["font_code_bold"]

    contact_card_data_uri = None
    if contact_card_path is not None and Path(contact_card_path).exists():
        contact_card_data_uri = _data_uri(Path(contact_card_path))

    env = _build_env()
    template = env.get_template("cheatsheet.html.jinja2")
    return template.render(
        cheatsheet=cheatsheet,
        diagrams=cheatsheet.get("diagrams") or [],
        reasoning_panel=cheatsheet.get("reasoning_panel"),
        code_html=highlight_python_html(cheatsheet.get("code", "")),
        width=image_cfg["final_width"],
        height=image_cfg["final_height"],
        contact_card_data_uri=contact_card_data_uri,
        contact_card_max_width=contact_cfg["max_width_px"],
        font_heading_data_uri=_data_uri(font_heading_path),
        font_body_data_uri=_data_uri(font_body_path),
        font_code_data_uri=_data_uri(font_code_path),
        font_code_bold_data_uri=_data_uri(font_code_bold_path),
    )


def render_cheatsheet(
    cheatsheet: dict,
    settings: dict,
    output_path: Path,
    *,
    contact_card_path: Path | None = None,
) -> QAResult:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    image_cfg = settings["image"]
    target_width = image_cfg["final_width"]
    target_height = image_cfg["final_height"]

    warnings: list[str] = []
    if contact_card_path is None or not Path(contact_card_path).exists():
        warnings.append("No contact card provided/found -- rendered without one.")

    html = _render_html(cheatsheet, settings, contact_card_path=contact_card_path)

    overflow_px = 0
    with sync_playwright() as pw:
        try:
            # channel="chromium" pins the full Chromium build Playwright has
            # always installed via `playwright install chromium`. Without
            # it, launch() silently prefers the separate
            # "chromium-headless-shell" binary for headless runs (default
            # since Playwright 1.45) -- which some Playwright versions
            # don't actually install even when `playwright install chromium`
            # (or `--with-deps chromium`) was run, breaking CI with
            # `Executable doesn't exist at .../chromium_headless_shell-*`
            # while the regular chromium build sits right there, installed
            # and unused. See ARCHITECTURE.md "Why HTML/CSS instead of
            # Pillow" for the renderer's Chromium dependency generally.
            browser = pw.chromium.launch(channel="chromium")
        except PlaywrightError as exc:
            if "Executable doesn't exist" in str(exc):
                raise RendererNotInstalledError(
                    "Playwright's Chromium browser isn't installed yet. This is a "
                    "one-time setup step separate from `pip install` -- run:\n\n"
                    "    playwright install chromium\n\n"
                    "(with your virtualenv activated), then re-run the pipeline."
                ) from exc
            raise
        try:
            page = browser.new_page(viewport={"width": target_width, "height": target_height})
            page.set_content(html, wait_until="load")
            # Fonts embedded as data: URIs still resolve asynchronously in
            # Chromium -- wait for them before measuring/screenshotting so
            # headline width (and therefore overflow) is measured against
            # final layout, not a fallback-font layout.
            page.evaluate("document.fonts.ready.then(() => true)")
            scroll_height = page.evaluate("document.querySelector('.page').scrollHeight")
            if scroll_height and scroll_height > target_height:
                overflow_px = int(scroll_height - target_height)
            page.screenshot(path=str(output_path))
        finally:
            browser.close()

    width, height = read_png_size(output_path)

    checks = {
        "exact_width": width == target_width,
        "exact_height": height == target_height,
        "correct_format": output_path.suffix.lower() == ".png",
        "headline_present": bool(cheatsheet.get("headline")),
        "code_present": bool(cheatsheet.get("code")),
        "no_overflow": overflow_px == 0,
    }
    if overflow_px:
        warnings.append(f"Content overflowed the canvas by ~{overflow_px}px.")

    passed = all(checks.values())

    return QAResult(
        passed=passed,
        width=width,
        height=height,
        format="PNG",
        checks=checks,
        warnings=warnings,
    )
