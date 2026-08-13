import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from src.rendering.render import render_cheatsheet
from tests.helpers import (
    load_sample_cheatsheet_json,
    load_sample_cheatsheet_no_diagram_json,
    load_sample_cheatsheet_sliding_window_json,
)

REPO_ROOT = Path(__file__).parent.parent


class RenderCheatsheetTests(unittest.TestCase):
    def setUp(self):
        self.config = yaml.safe_load((REPO_ROOT / "config" / "settings.yaml").read_text())
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

    def test_renders_exact_canvas_size_and_format(self):
        output_path = Path(self.tmpdir.name) / "cheatsheet.png"
        result = render_cheatsheet(
            load_sample_cheatsheet_json(), self.config, output_path, contact_card_path=None
        )
        self.assertTrue(output_path.exists())
        self.assertEqual(result.width, 1080)
        self.assertEqual(result.height, 1350)
        self.assertEqual(result.format, "PNG")
        self.assertTrue(result.checks["exact_width"])
        self.assertTrue(result.checks["exact_height"])
        self.assertTrue(result.checks["correct_format"])
        self.assertTrue(result.checks["headline_present"])
        self.assertTrue(result.checks["code_present"])
        # No contact card provided -> should warn, not crash.
        self.assertTrue(any("contact card" in w for w in result.warnings))

    def test_renders_array_pointers_diagram_without_error(self):
        # sample_cheatsheet.json carries a single array_pointers diagram --
        # exercise that component path specifically (see the diagram
        # library in ARCHITECTURE.md / prompts/claude/v1/solve.md).
        output_path = Path(self.tmpdir.name) / "cheatsheet_array_pointers.png"
        cheatsheet = load_sample_cheatsheet_json()
        self.assertEqual(cheatsheet["diagrams"][0]["component"], "array_pointers")
        result = render_cheatsheet(cheatsheet, self.config, output_path, contact_card_path=None)
        self.assertTrue(result.passed)

    def test_renders_comparison_states_diagram_and_reasoning_panel(self):
        output_path = Path(self.tmpdir.name) / "cheatsheet_comparison.png"
        cheatsheet = load_sample_cheatsheet_sliding_window_json()
        self.assertEqual(cheatsheet["diagrams"][0]["component"], "comparison_states")
        self.assertIsNotNone(cheatsheet["reasoning_panel"])
        result = render_cheatsheet(cheatsheet, self.config, output_path, contact_card_path=None)
        self.assertTrue(result.passed, msg=result.failed_checks)
        self.assertEqual(result.width, 1080)
        self.assertEqual(result.height, 1350)

    def test_renders_text_only_fallback_when_no_diagrams(self):
        # sample_cheatsheet_no_diagram.json has neither `diagrams` nor
        # `reasoning_panel` -- the renderer must still produce a valid,
        # non-crashing single-column layout (see cheatsheet.html.jinja2's
        # `{% if not diagrams %}` fallback branch).
        output_path = Path(self.tmpdir.name) / "cheatsheet_no_diagram.png"
        cheatsheet = load_sample_cheatsheet_no_diagram_json()
        self.assertNotIn("diagrams", cheatsheet)
        result = render_cheatsheet(cheatsheet, self.config, output_path, contact_card_path=None)
        self.assertTrue(result.passed, msg=result.failed_checks)

    def test_embeds_contact_card_when_path_given(self):
        contact_card_path = REPO_ROOT / self.config["contact_card"]["path"]
        if not contact_card_path.exists():
            self.skipTest("No contact card asset present in this checkout.")
        output_path = Path(self.tmpdir.name) / "cheatsheet_with_contact.png"
        result = render_cheatsheet(
            load_sample_cheatsheet_json(),
            self.config,
            output_path,
            contact_card_path=contact_card_path,
        )
        self.assertTrue(result.passed)
        self.assertFalse(any("contact card" in w for w in result.warnings))

    def test_missing_contact_card_path_warns_but_does_not_crash(self):
        output_path = Path(self.tmpdir.name) / "cheatsheet_missing_contact.png"
        result = render_cheatsheet(
            load_sample_cheatsheet_json(),
            self.config,
            output_path,
            contact_card_path=REPO_ROOT / "assets" / "does-not-exist.png",
        )
        self.assertTrue(output_path.exists())
        self.assertTrue(any("contact card" in w for w in result.warnings))

    def test_launches_with_channel_chromium_not_default_headless_shell(self):
        # Regression: launch() must pin channel="chromium" so it always
        # uses the full Chromium build `playwright install chromium`
        # reliably installs, instead of silently preferring the separate
        # chromium-headless-shell binary Playwright defaults to for
        # headless runs since v1.45 -- some Playwright versions don't
        # actually install that binary even when `playwright install
        # chromium` (or `--with-deps chromium`) was run, which broke CI
        # with `Executable doesn't exist at .../chromium_headless_shell-*`
        # while the regular chromium sat there installed and unused. See
        # CHANGELOG.md's entry on this fix. Mocked (no real browser launch)
        # so this test isolates the launch() call itself, independent of
        # the real-Chromium rendering already covered by the tests above.
        output_path = Path(self.tmpdir.name) / "cheatsheet_channel_check.png"

        fake_page = mock.MagicMock()
        fake_page.evaluate.return_value = 100  # small scroll height, no overflow
        fake_browser = mock.MagicMock()
        fake_browser.new_page.return_value = fake_page
        fake_pw = mock.MagicMock()
        fake_pw.chromium.launch.return_value = fake_browser
        fake_sync_playwright_cm = mock.MagicMock()
        fake_sync_playwright_cm.__enter__.return_value = fake_pw
        fake_sync_playwright_cm.__exit__.return_value = False

        with mock.patch(
            "src.rendering.render.sync_playwright", return_value=fake_sync_playwright_cm
        ), mock.patch("src.rendering.render.read_png_size", return_value=(1080, 1350)):
            render_cheatsheet(
                load_sample_cheatsheet_json(), self.config, output_path, contact_card_path=None
            )

        fake_pw.chromium.launch.assert_called_once_with(channel="chromium")

    def test_long_content_is_flagged_as_overflow_not_crash_or_resize(self):
        cheatsheet = load_sample_cheatsheet_json()
        cheatsheet["intuition"] = ("This is a very long intuition sentence. " * 30).strip()
        cheatsheet["code"] = cheatsheet["code"] + "\n" + "\n".join(
            f"    # padding comment line {i} to stress vertical fit" for i in range(60)
        )
        output_path = Path(self.tmpdir.name) / "cheatsheet_long.png"
        result = render_cheatsheet(cheatsheet, self.config, output_path, contact_card_path=None)
        # Must always produce a valid PNG at the exact canvas size even
        # when content is too long -- overflow is reported as a failed
        # check for the caller to act on, never a crash or a resized canvas.
        self.assertEqual(result.width, 1080)
        self.assertEqual(result.height, 1350)
        self.assertFalse(result.checks["no_overflow"])
        self.assertFalse(result.passed)
        self.assertTrue(any("overflow" in w for w in result.warnings))


if __name__ == "__main__":
    unittest.main()
