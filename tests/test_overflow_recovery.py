"""Tests for src.rendering.existing_provider.render_with_overflow_recovery --
the deterministic,
zero-extra-API-cost fallback for a render that overflows the fixed
1080x1350 canvas: drop reasoning_panel, then diagrams, and re-render,
instead of re-invoking Claude to re-compress. See CHANGELOG.md and
docs/OPERATIONS.md "When the renderer's QA gate fails"."""

import unittest
from unittest import mock

from src.rendering.render import QAResult


def _qa(passed, failing_check=None):
    """Builds a QAResult whose `checks` dict mirrors render_cheatsheet()'s
    real shape, with exactly `failing_check` (or nothing) failing."""
    checks = {
        "exact_width": True,
        "exact_height": True,
        "correct_format": True,
        "headline_present": True,
        "code_present": True,
        "no_overflow": failing_check != "no_overflow",
    }
    if failing_check and failing_check != "no_overflow":
        checks[failing_check] = False
    return QAResult(passed=passed, width=1080, height=1350, format="PNG", checks=checks)


class RenderWithOverflowRecoveryTests(unittest.TestCase):
    def _cheatsheet(self, **extra):
        base = {"problem": {"number": 1}, "headline": "h", "code": "c"}
        base.update(extra)
        return base

    def test_drops_reasoning_panel_when_only_overflow_fails_and_it_then_fits(self):
        cheatsheet = self._cheatsheet(reasoning_panel={"text": "why this works"})
        overflow = _qa(passed=False, failing_check="no_overflow")
        fits = _qa(passed=True)

        with mock.patch(
            "src.rendering.render.render_cheatsheet", side_effect=[overflow, fits]
        ) as mock_render:
            from src.rendering.existing_provider import render_with_overflow_recovery

            qa, dropped = render_with_overflow_recovery(cheatsheet, {}, "img.png", None)

        self.assertEqual(mock_render.call_count, 2)
        self.assertTrue(qa.passed)
        self.assertEqual(dropped, ["reasoning_panel"])
        self.assertNotIn("reasoning_panel", cheatsheet)

    def test_drops_diagrams_too_when_reasoning_panel_alone_is_not_enough(self):
        cheatsheet = self._cheatsheet(
            reasoning_panel={"text": "why this works"},
            diagrams=[{"kind": "array_pointers"}],
        )
        still_overflowing = _qa(passed=False, failing_check="no_overflow")
        fits = _qa(passed=True)

        with mock.patch(
            "src.rendering.render.render_cheatsheet",
            side_effect=[still_overflowing, still_overflowing, fits],
        ) as mock_render:
            from src.rendering.existing_provider import render_with_overflow_recovery

            qa, dropped = render_with_overflow_recovery(cheatsheet, {}, "img.png", None)

        self.assertEqual(mock_render.call_count, 3)
        self.assertTrue(qa.passed)
        self.assertEqual(dropped, ["reasoning_panel", "diagrams"])
        self.assertNotIn("reasoning_panel", cheatsheet)
        self.assertNotIn("diagrams", cheatsheet)

    def test_gives_up_cleanly_once_nothing_droppable_is_left(self):
        # No reasoning_panel/diagrams present at all -- genuine overflow,
        # not a dropped-section-would-fix-it case.
        cheatsheet = self._cheatsheet()
        still_overflowing = _qa(passed=False, failing_check="no_overflow")

        with mock.patch(
            "src.rendering.render.render_cheatsheet", return_value=still_overflowing
        ) as mock_render:
            from src.rendering.existing_provider import render_with_overflow_recovery

            qa, dropped = render_with_overflow_recovery(cheatsheet, {}, "img.png", None)

        mock_render.assert_called_once()
        self.assertFalse(qa.passed)
        self.assertEqual(dropped, [])

    def test_does_not_drop_anything_on_a_non_overflow_failure(self):
        # A different QA check failing (e.g. exact_width) is not something
        # dropping optional sections can fix -- must not retry or mutate.
        cheatsheet = self._cheatsheet(reasoning_panel={"text": "why this works"})
        wrong_size = _qa(passed=False, failing_check="exact_width")

        with mock.patch(
            "src.rendering.render.render_cheatsheet", return_value=wrong_size
        ) as mock_render:
            from src.rendering.existing_provider import render_with_overflow_recovery

            qa, dropped = render_with_overflow_recovery(cheatsheet, {}, "img.png", None)

        mock_render.assert_called_once()
        self.assertFalse(qa.passed)
        self.assertEqual(dropped, [])
        self.assertIn("reasoning_panel", cheatsheet)


if __name__ == "__main__":
    unittest.main()
