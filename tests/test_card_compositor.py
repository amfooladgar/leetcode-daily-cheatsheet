"""Unit tests for src/rendering/card_compositor.py in isolation from the
OpenAI provider -- bounds/margins, aspect-ratio preservation, alpha
handling, and the "never mutate the source card" invariant (see
ARCHITECTURE.md "Optional OpenAI image renderer")."""

import hashlib
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from src.rendering.card_compositor import (
    CardCompositeError,
    composite_card,
    compute_card_box,
    compute_reserved_region,
    detect_blank_region,
    validate_card,
)


def _save_png(path: Path, width: int, height: int, color=(200, 30, 30, 255)) -> None:
    Image.new("RGBA", (width, height), color).save(path, format="PNG")


def _hash(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class ComputeCardBoxTests(unittest.TestCase):
    def test_native_size_kept_when_it_fits(self):
        box = compute_card_box(100, 50, canvas_width=1000, canvas_height=800, margin_right=20, margin_bottom=20)
        self.assertEqual(box, (100, 50))

    def test_scales_down_proportionally_when_too_wide(self):
        box = compute_card_box(2000, 500, canvas_width=1000, canvas_height=800, margin_right=20, margin_bottom=20)
        # available width = 980, scale = 980/2000 = 0.49
        self.assertEqual(box, (980, 245))
        self.assertAlmostEqual(box[0] / box[1], 2000 / 500, places=2)

    def test_scales_down_proportionally_when_too_tall(self):
        box = compute_card_box(500, 2000, canvas_width=1000, canvas_height=800, margin_right=20, margin_bottom=20)
        available_height = 780
        scale = available_height / 2000
        self.assertEqual(box, (round(500 * scale), round(2000 * scale)))

    def test_raises_when_margins_leave_no_room(self):
        with self.assertRaises(CardCompositeError):
            compute_card_box(100, 50, canvas_width=100, canvas_height=100, margin_right=150, margin_bottom=10)


class ComputeReservedRegionTests(unittest.TestCase):
    def test_pads_native_size_box_by_default_20_percent(self):
        # A live smoke test showed GPT Image under-reserving relative to an
        # exact-pixel request -- see CHANGELOG.md and prompts/openai/README.md.
        width, height = compute_reserved_region(
            100, 50, canvas_width=1000, canvas_height=800, margin_right=20, margin_bottom=20
        )
        self.assertEqual((width, height), (120, 60))

    def test_padding_never_shrinks_the_reservation(self):
        width, height = compute_reserved_region(
            100, 50, canvas_width=1000, canvas_height=800, margin_right=20, margin_bottom=20, safety_margin=0
        )
        self.assertEqual((width, height), (100, 50))

    def test_padding_is_clamped_to_available_canvas_space(self):
        # Card box already nearly fills the available area -- a naive 20%
        # pad would exceed the canvas, so it must be clamped, not raise.
        width, height = compute_reserved_region(
            950, 750, canvas_width=1000, canvas_height=800, margin_right=20, margin_bottom=20
        )
        self.assertEqual((width, height), (980, 780))  # clamped to available_width/height

    def test_uses_scaled_box_when_card_larger_than_canvas(self):
        # compute_card_box() would scale a 5000x2000 card down to fit;
        # compute_reserved_region() must pad *that* scaled size, not the
        # raw native size.
        expected_box = compute_card_box(
            5000, 2000, canvas_width=1000, canvas_height=800, margin_right=20, margin_bottom=20
        )
        width, height = compute_reserved_region(
            5000, 2000, canvas_width=1000, canvas_height=800, margin_right=20, margin_bottom=20, safety_margin=0
        )
        self.assertEqual((width, height), expected_box)


class DetectBlankRegionTests(unittest.TestCase):
    """Direct unit tests for the algorithm behind the fix in CHANGELOG.md:
    a live smoke test showed the *requested* reservation size (even
    padded) doesn't reliably match what GPT Image actually leaves blank,
    so the compositor measures the real background instead."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.canvas_width = 100
        self.canvas_height = 80
        self.margin_right = 10
        self.margin_bottom = 10
        self.max_width = 40
        self.max_height = 30
        # anchor_x = 89, anchor_y = 69 for these dimensions.

    def _kwargs(self, **overrides):
        kwargs = dict(
            canvas_width=self.canvas_width,
            canvas_height=self.canvas_height,
            position="bottom-right",
            margin_right=self.margin_right,
            margin_bottom=self.margin_bottom,
            max_width=self.max_width,
            max_height=self.max_height,
        )
        kwargs.update(overrides)
        return kwargs

    def _save(self, image: Image.Image) -> Path:
        path = Path(self.tmpdir.name) / "background.png"
        image.save(path, format="PNG")
        return path

    def test_fully_blank_background_detects_the_full_capped_window(self):
        bg = Image.new("RGB", (self.canvas_width, self.canvas_height), (255, 255, 255))
        path = self._save(bg)
        width, height = detect_blank_region(path, **self._kwargs())
        self.assertEqual((width, height), (self.max_width, self.max_height))

    def test_content_band_above_the_corner_reduces_detected_height_only(self):
        # Everything at y <= 43 is "content" (dark); y >= 44 is blank --
        # mimics a text panel sitting above the reserved corner. All width
        # probe rows (as low as y=44) stay inside the blank zone, so only
        # height should shrink.
        bg = Image.new("RGB", (self.canvas_width, self.canvas_height), (255, 255, 255))
        for y in range(0, 44):
            for x in range(self.canvas_width):
                bg.putpixel((x, y), (20, 20, 20))
        path = self._save(bg)
        width, height = detect_blank_region(path, **self._kwargs())
        self.assertEqual(height, 26)  # rows 69..44 inclusive = 26 blank rows
        self.assertEqual(width, self.max_width)  # unaffected, capped at max

    def test_content_band_left_of_the_corner_reduces_detected_width_only(self):
        # Everything at x <= 50 is "content"; x >= 51 is blank. All height
        # probe columns (as far left as x=56) stay inside the blank zone,
        # so only width should shrink.
        bg = Image.new("RGB", (self.canvas_width, self.canvas_height), (255, 255, 255))
        for x in range(0, 51):
            for y in range(self.canvas_height):
                bg.putpixel((x, y), (20, 20, 20))
        path = self._save(bg)
        width, height = detect_blank_region(path, **self._kwargs())
        self.assertEqual(width, 39)  # x=89..51 inclusive = 39 blank columns
        self.assertEqual(height, self.max_height)  # unaffected, capped at max

    def test_content_touching_the_corner_pixel_returns_zero(self):
        bg = Image.new("RGB", (self.canvas_width, self.canvas_height), (20, 20, 20))
        path = self._save(bg)
        width, height = detect_blank_region(path, **self._kwargs())
        self.assertEqual((width, height), (0, 0))

    def test_thin_border_line_does_not_zero_out_detection(self):
        # Regression for a live smoke test: a single hairline panel-border
        # pixel a few units off white otherwise collapsed the whole
        # measurement to (0, 0) even though the real generated background
        # had a large, genuinely blank area around it (see CHANGELOG.md).
        # A 2px-wide near-white border crossing one probe line, with real
        # blank space on both sides, must not zero out that line.
        bg = Image.new("RGB", (self.canvas_width, self.canvas_height), (255, 255, 255))
        border_y = 50  # inside the max_height=30 window from anchor_y=69
        for x in range(self.canvas_width):
            bg.putpixel((x, border_y), (238, 246, 254))  # ~17 units off white
            bg.putpixel((x, border_y + 1), (238, 246, 254))
        path = self._save(bg)
        width, height = detect_blank_region(path, **self._kwargs())
        # Height should still measure past the thin border, not collapse.
        self.assertGreater(height, 15)
        self.assertEqual(width, self.max_width)

    def test_thick_content_band_still_stops_detection(self):
        # A sustained non-blank run (wider than max_gap) must still be
        # treated as real content, not skipped over like a hairline border.
        bg = Image.new("RGB", (self.canvas_width, self.canvas_height), (255, 255, 255))
        for y in range(40, 60):  # 20px band -- much wider than max_gap
            for x in range(self.canvas_width):
                bg.putpixel((x, y), (20, 20, 20))
        path = self._save(bg)
        width, height = detect_blank_region(path, **self._kwargs())
        self.assertEqual(height, 10)  # rows 69..60 inclusive stay blank before hitting the band

    def test_width_probes_are_bounded_to_the_detected_height_not_max_height(self):
        # Regression for a live smoke test (see CHANGELOG.md): a panel
        # positioned beyond the height the card will actually use (because
        # height itself came out well below max_height) was still zeroing
        # out width, since width probes used to reach all the way to
        # max_height regardless of the real, smaller usable height.
        # Content fills y in [0, 54] (so height detection stops at 15, well
        # below max_height=30); if width probing still reached the old,
        # deeper max_height window, it would hit this same content and
        # report 0 -- bounding width probes to the real detected height
        # must keep width unaffected instead.
        bg = Image.new("RGB", (self.canvas_width, self.canvas_height), (255, 255, 255))
        for y in range(0, 55):
            for x in range(self.canvas_width):
                bg.putpixel((x, y), (20, 20, 20))
        path = self._save(bg)
        width, height = detect_blank_region(path, **self._kwargs())
        self.assertEqual(height, 15)  # rows 69..55 inclusive stay blank
        self.assertEqual(width, self.max_width)  # unaffected -- capped at max

    def test_reference_color_is_not_sampled_from_the_corner_pixel(self):
        # Regression: if the corner pixel itself is content (e.g. blue),
        # sampling "blank" from that pixel would make surrounding
        # same-colored content look blank too. Using the configured
        # background_hex instead means a non-white corner is correctly
        # detected as non-blank.
        bg = Image.new("RGB", (self.canvas_width, self.canvas_height), (30, 60, 200))
        path = self._save(bg)
        width, height = detect_blank_region(path, background_hex="#FFFFFF", **self._kwargs())
        self.assertEqual((width, height), (0, 0))

    def test_bottom_left_position_scans_inward_from_the_left_margin(self):
        # anchor_x = margin_right = 10 for bottom-left, scanning rightward.
        bg = Image.new("RGB", (self.canvas_width, self.canvas_height), (255, 255, 255))
        # Content from x=45 onward (still inside the max_width=40 window
        # measured from anchor_x=10) should reduce width but not height,
        # since all height-probe columns (x <= 43) stay clear of it.
        for x in range(45, self.canvas_width):
            for y in range(self.canvas_height):
                bg.putpixel((x, y), (20, 20, 20))
        path = self._save(bg)
        width, height = detect_blank_region(path, **self._kwargs(position="bottom-left"))
        self.assertEqual(width, 35)  # x=10..44 inclusive = 35 blank columns
        self.assertEqual(height, self.max_height)

    def test_unsupported_position_raises(self):
        bg = Image.new("RGB", (self.canvas_width, self.canvas_height), (255, 255, 255))
        path = self._save(bg)
        with self.assertRaises(CardCompositeError):
            detect_blank_region(path, **self._kwargs(position="top-center"))


class ValidateCardTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

    def test_missing_file_raises(self):
        with self.assertRaises(CardCompositeError):
            validate_card(Path(self.tmpdir.name) / "nope.png")

    def test_invalid_image_raises(self):
        bad = Path(self.tmpdir.name) / "bad.png"
        bad.write_bytes(b"definitely not a png")
        with self.assertRaises(CardCompositeError):
            validate_card(bad)

    def test_valid_image_does_not_raise(self):
        card = Path(self.tmpdir.name) / "card.png"
        _save_png(card, 40, 20)
        validate_card(card)  # should not raise


class CompositeCardTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.canvas_width = 200
        self.canvas_height = 100

        self.background_path = Path(self.tmpdir.name) / "background.png"
        _save_png(self.background_path, self.canvas_width, self.canvas_height, (255, 255, 255, 255))

        self.card_path = Path(self.tmpdir.name) / "card.png"
        _save_png(self.card_path, 40, 20, (10, 20, 30, 255))

        self.output_path = Path(self.tmpdir.name) / "final.png"

    def _composite(self, **overrides):
        kwargs = dict(
            canvas_width=self.canvas_width,
            canvas_height=self.canvas_height,
            position="bottom-right",
            margin_right=10,
            margin_bottom=5,
            clear_hex="#FFFFFF",
        )
        kwargs.update(overrides)
        composite_card(self.background_path, self.card_path, self.output_path, **kwargs)

    def test_produces_final_image_at_canvas_size(self):
        self._composite()
        with Image.open(self.output_path) as img:
            self.assertEqual(img.size, (self.canvas_width, self.canvas_height))

    def test_source_card_file_is_never_modified(self):
        before = _hash(self.card_path)
        self._composite()
        after = _hash(self.card_path)
        self.assertEqual(before, after)

    def test_background_file_is_never_modified(self):
        before = _hash(self.background_path)
        self._composite()
        after = _hash(self.background_path)
        self.assertEqual(before, after)

    def test_bottom_right_placement_respects_margins(self):
        self._composite(margin_right=10, margin_bottom=5)
        with Image.open(self.output_path) as img:
            rgb = img.convert("RGB")
            # The card is fully opaque (10, 20, 30) -- check a pixel inside
            # the expected placed region matches the source color exactly.
            expected_x = self.canvas_width - 10 - 40 + 5  # a few px inside the card's left edge
            expected_y = self.canvas_height - 5 - 20 + 5
            self.assertEqual(rgb.getpixel((expected_x, expected_y)), (10, 20, 30))

    def test_bottom_left_placement(self):
        self._composite(position="bottom-left", margin_right=10, margin_bottom=5)
        with Image.open(self.output_path) as img:
            rgb = img.convert("RGB")
            expected_x = 10 + 5
            expected_y = self.canvas_height - 5 - 20 + 5
            self.assertEqual(rgb.getpixel((expected_x, expected_y)), (10, 20, 30))

    def test_unsupported_position_raises(self):
        with self.assertRaises(CardCompositeError):
            self._composite(position="top-center")

    def test_native_size_pixels_preserved_at_fully_opaque_region(self):
        self._composite()
        with Image.open(self.card_path) as card:
            card_rgb = card.convert("RGB")
        with Image.open(self.output_path) as final:
            final_rgb = final.convert("RGB")
        x = self.canvas_width - 10 - 40 + 20  # center-ish of the card region
        y = self.canvas_height - 5 - 20 + 10
        self.assertEqual(final_rgb.getpixel((x, y)), card_rgb.getpixel((20, 10)))

    def test_scales_down_when_card_larger_than_canvas(self):
        big_card = Path(self.tmpdir.name) / "big_card.png"
        _save_png(big_card, 5000, 2000, (5, 6, 7, 255))
        composite_card(
            self.background_path,
            big_card,
            self.output_path,
            canvas_width=self.canvas_width,
            canvas_height=self.canvas_height,
            position="bottom-right",
            margin_right=10,
            margin_bottom=5,
        )
        with Image.open(self.output_path) as img:
            self.assertEqual(img.size, (self.canvas_width, self.canvas_height))
        before_hash = _hash(big_card)
        self.assertEqual(_hash(big_card), before_hash)  # source untouched even when scaled

    def test_uses_alpha_channel_as_paste_mask(self):
        transparent_card = Path(self.tmpdir.name) / "transparent.png"
        img = Image.new("RGBA", (10, 10), (255, 0, 0, 0))  # fully transparent
        img.save(transparent_card, format="PNG")
        composite_card(
            self.background_path,
            transparent_card,
            self.output_path,
            canvas_width=self.canvas_width,
            canvas_height=self.canvas_height,
            position="bottom-right",
            margin_right=10,
            margin_bottom=5,
            clear_hex="#00FF00",
        )
        with Image.open(self.output_path) as final:
            rgb = final.convert("RGB")
            x = self.canvas_width - 10 - 5
            y = self.canvas_height - 5 - 5
            # A fully transparent card pixel must show the clear color
            # underneath, not the (ignored) red RGB of the source pixel.
            self.assertEqual(rgb.getpixel((x, y)), (0, 255, 0))

    def test_missing_background_raises(self):
        with self.assertRaises(CardCompositeError):
            composite_card(
                Path(self.tmpdir.name) / "no-bg.png",
                self.card_path,
                self.output_path,
                canvas_width=self.canvas_width,
                canvas_height=self.canvas_height,
                position="bottom-right",
                margin_right=10,
                margin_bottom=5,
            )

    def test_wrong_background_size_raises(self):
        wrong_bg = Path(self.tmpdir.name) / "wrong.png"
        _save_png(wrong_bg, 50, 50)
        with self.assertRaises(CardCompositeError):
            composite_card(
                wrong_bg,
                self.card_path,
                self.output_path,
                canvas_width=self.canvas_width,
                canvas_height=self.canvas_height,
                position="bottom-right",
                margin_right=10,
                margin_bottom=5,
            )


if __name__ == "__main__":
    unittest.main()
