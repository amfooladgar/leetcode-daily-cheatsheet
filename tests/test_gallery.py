"""Tests for src/state/gallery.py and scripts/build_gallery.py -- offline,
no network access, matching CLAUDE.md's testing rule."""

import tempfile
import unittest
from pathlib import Path

from scripts.build_gallery import build_gallery, collect_cards, render_site
from src.state.gallery import save_gallery_image
from src.state.manifest import Manifest, ManifestEntry, save

FILENAME_PATTERN = "{number}-{slug}-{date}"


def _png_bytes() -> bytes:
    return b"\x89PNG\r\n\x1a\nfake-png-bytes"


def _success_entry(**overrides) -> ManifestEntry:
    fields = dict(
        date="2026-08-13",
        problem_number=1,
        slug="two-sum",
        status="success",
        content_hash="abc123",
        image_filename="cheatsheet.png",
        drive=True,
        title="Two Sum",
        difficulty="Easy",
        topics=["Array", "Hash Table"],
        headline="Never Forget the One-Pass Hash Map Trick",
        problem_url="https://leetcode.com/problems/two-sum/",
    )
    fields.update(overrides)
    return ManifestEntry(**fields)


class SaveGalleryImageTests(unittest.TestCase):
    def test_copies_image_to_named_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "cheatsheet.png"
            src.write_bytes(_png_bytes())
            images_dir = tmp_path / "gallery" / "images"

            dest = save_gallery_image(src, images_dir, "1-two-sum-2026-08-13")

            self.assertEqual(dest, images_dir / "1-two-sum-2026-08-13.png")
            self.assertEqual(dest.read_bytes(), _png_bytes())


class CollectCardsTests(unittest.TestCase):
    def test_only_published_entries_with_an_image_are_included(self):
        manifest = Manifest()
        manifest.record(_success_entry())
        manifest.record(_success_entry(problem_number=2, slug="failed-run", status="failed"))
        manifest.record(
            _success_entry(problem_number=3, slug="no-drive", date="2026-08-14", drive=False)
        )
        manifest.record(_success_entry(problem_number=4, slug="missing-image", date="2026-08-15"))

        with tempfile.TemporaryDirectory() as tmp:
            images_dir = Path(tmp)
            (images_dir / "1-two-sum-2026-08-13.png").write_bytes(_png_bytes())
            # No image written for problem 4 -- must be skipped, not crash.

            cards = collect_cards(manifest, images_dir, FILENAME_PATTERN)

        self.assertEqual(len(cards), 1)
        card = cards[0]
        self.assertEqual(card.number, 1)
        self.assertEqual(card.title, "Two Sum")
        self.assertEqual(card.difficulty, "Easy")
        self.assertEqual(card.topics, ["Array", "Hash Table"])
        self.assertEqual(card.image_filename, "1-two-sum-2026-08-13.png")

    def test_cards_sorted_by_date_then_number_descending(self):
        manifest = Manifest()
        manifest.record(_success_entry(problem_number=1, date="2026-08-10"))
        manifest.record(_success_entry(problem_number=5, slug="p5", date="2026-08-12"))
        manifest.record(_success_entry(problem_number=2, slug="p2", date="2026-08-12"))

        with tempfile.TemporaryDirectory() as tmp:
            images_dir = Path(tmp)
            for stem in ["1-two-sum-2026-08-10", "5-p5-2026-08-12", "2-p2-2026-08-12"]:
                (images_dir / f"{stem}.png").write_bytes(_png_bytes())

            cards = collect_cards(manifest, images_dir, FILENAME_PATTERN)

        self.assertEqual(
            [(c.date, c.number) for c in cards],
            [
                ("2026-08-12", 5),
                ("2026-08-12", 2),
                ("2026-08-10", 1),
            ],
        )

    def test_empty_manifest_yields_no_cards(self):
        with tempfile.TemporaryDirectory() as tmp:
            cards = collect_cards(Manifest(), Path(tmp), FILENAME_PATTERN)
        self.assertEqual(cards, [])


class RenderSiteTests(unittest.TestCase):
    def test_renders_cards_with_filter_attributes(self):
        manifest = Manifest()
        manifest.record(_success_entry())
        with tempfile.TemporaryDirectory() as tmp:
            images_dir = Path(tmp)
            (images_dir / "1-two-sum-2026-08-13.png").write_bytes(_png_bytes())
            cards = collect_cards(manifest, images_dir, FILENAME_PATTERN)

        html = render_site(cards, "Test Gallery")

        self.assertIn("Test Gallery", html)
        self.assertIn("#1 Two Sum", html)
        self.assertIn('data-difficulty="Easy"', html)
        self.assertIn("Array", html)
        self.assertIn("images/1-two-sum-2026-08-13.png", html)

    def test_renders_without_crashing_when_no_cards(self):
        html = render_site([], "Test Gallery")
        self.assertIn("0 published cheat sheets", html)


class BuildGalleryTests(unittest.TestCase):
    def test_end_to_end_build_writes_index_and_copies_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            manifest_path = tmp_path / "manifest.json"
            images_dir = tmp_path / "gallery-images"
            site_dir = tmp_path / "site"

            manifest = Manifest()
            manifest.record(_success_entry())
            save(manifest, manifest_path)
            images_dir.mkdir()
            (images_dir / "1-two-sum-2026-08-13.png").write_bytes(_png_bytes())

            count = build_gallery(
                manifest_path, images_dir, site_dir, FILENAME_PATTERN, "Test Gallery"
            )

            self.assertEqual(count, 1)
            self.assertTrue((site_dir / "index.html").exists())
            self.assertTrue((site_dir / "images" / "1-two-sum-2026-08-13.png").exists())

    def test_rebuild_clears_stale_site_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            manifest_path = tmp_path / "manifest.json"
            images_dir = tmp_path / "gallery-images"
            site_dir = tmp_path / "site"
            save(Manifest(), manifest_path)
            images_dir.mkdir()
            site_dir.mkdir()
            stale_file = site_dir / "images" / "stale-old-entry.png"
            stale_file.parent.mkdir(parents=True)
            stale_file.write_bytes(_png_bytes())

            build_gallery(manifest_path, images_dir, site_dir, FILENAME_PATTERN, "Test Gallery")

            self.assertFalse(stale_file.exists())


if __name__ == "__main__":
    unittest.main()
