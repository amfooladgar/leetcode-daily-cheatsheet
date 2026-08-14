"""The "existing" image_generation provider: a thin adapter around
src/rendering/render.py's deterministic HTML/CSS + Playwright renderer, so
src/rendering/factory.py can dispatch to it with the same call shape as
src/rendering/openai_provider.py.

render_with_overflow_recovery() and _OVERFLOW_FALLBACK_KEYS were moved here
verbatim from src/main.py (see CHANGELOG.md's "canvas-overflow" entry for
the original rationale) as part of adding the optional OpenAI provider --
their behavior is unchanged, only their home module is. tests/
test_overflow_recovery.py imports render_with_overflow_recovery from here.
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.rendering.base import RenderResult

log = logging.getLogger(__name__)

# Optional, droppable sections tried in this order when a render overflows
# the fixed canvas -- reasoning_panel first since it's the more purely
# decorative of the two (a closing "why this works" callout), diagrams
# second since prompts/claude/v1/solve.md already treats a dropped diagram
# as an acceptable outcome ("A wrong or forced diagram is worse than no
# diagram" -- see ARCHITECTURE.md "Diagram component library").
_OVERFLOW_FALLBACK_KEYS = ["reasoning_panel", "diagrams"]


def render_with_overflow_recovery(cheatsheet, settings, image_path, contact_card_path):
    """Renders `cheatsheet`, and if the *only* failed QA check is
    `no_overflow`, retries with optional decorative sections dropped one at
    a time (see `_OVERFLOW_FALLBACK_KEYS`) before giving up. This is a
    free, deterministic recovery -- no extra Claude calls, no re-compress
    -- for otherwise-correct, well-compressed content that's a few dozen
    pixels too tall for the fixed 1080x1350 canvas because of one optional
    section. If content still overflows with everything droppable already
    dropped, that's a genuine content-length problem (see
    docs/OPERATIONS.md "When the renderer's QA gate fails") and this
    returns the final, still-failing QAResult for the caller to handle as
    before.

    Returns (qa_result, dropped_keys) -- `dropped_keys` is empty unless a
    recovery attempt actually happened, so callers can tell a clean pass
    from a recovered one.
    """
    from src.rendering.render import render_cheatsheet  # deferred: needs Playwright

    dropped: list[str] = []
    qa = render_cheatsheet(cheatsheet, settings, image_path, contact_card_path=contact_card_path)

    for key in _OVERFLOW_FALLBACK_KEYS:
        if qa.passed or qa.failed_checks != ["no_overflow"] or key not in cheatsheet:
            break
        cheatsheet.pop(key, None)
        dropped.append(key)
        log.warning(
            "Content overflowed the canvas; retrying with '%s' dropped "
            "(deterministic, no extra API cost).",
            key,
        )
        qa = render_cheatsheet(
            cheatsheet, settings, image_path, contact_card_path=contact_card_path
        )

    return qa, dropped


def render(cheatsheet, settings, image_path, *, contact_card_path) -> RenderResult:
    """The existing provider's entry point for src/rendering/factory.py."""
    qa, dropped = render_with_overflow_recovery(cheatsheet, settings, image_path, contact_card_path)
    return RenderResult(
        provider="existing",
        passed=qa.passed,
        width=qa.width,
        height=qa.height,
        format=qa.format,
        image_path=Path(image_path),
        checks=qa.checks,
        warnings=qa.warnings,
        dropped_for_overflow=dropped,
    )
