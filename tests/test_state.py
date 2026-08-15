import tempfile
import unittest
from pathlib import Path

from src.state.manifest import Manifest, ManifestEntry, content_hash, load, save
from tests.helpers import load_sample_cheatsheet_json


class ManifestTests(unittest.TestCase):
    def test_roundtrip_save_and_load(self):
        manifest = Manifest()
        manifest.record(
            ManifestEntry(
                date="2026-08-13",
                problem_number=1,
                slug="two-sum",
                status="success",
                content_hash="abc123",
                image_filename="1-two-sum-2026-08-13.png",
                drive=True,
                drive_file_id="fileid123",
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            save(manifest, path)
            reloaded = load(path)

        self.assertTrue(reloaded.already_published("2026-08-13", 1))
        self.assertFalse(reloaded.already_published("2026-08-13", 2))
        self.assertFalse(reloaded.already_published("2026-08-14", 1))

    def test_roundtrip_preserves_gallery_metadata(self):
        manifest = Manifest()
        manifest.record(
            ManifestEntry(
                date="2026-08-13",
                problem_number=1,
                slug="two-sum",
                status="success",
                content_hash="abc123",
                image_filename="1-two-sum-2026-08-13.png",
                drive=True,
                title="Two Sum",
                difficulty="Easy",
                topics=["Array", "Hash Table"],
                headline="Never Forget the One-Pass Hash Map Trick",
                problem_url="https://leetcode.com/problems/two-sum/",
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            save(manifest, path)
            reloaded = load(path)

        entry = reloaded.get("2026-08-13", 1)
        self.assertEqual(entry.title, "Two Sum")
        self.assertEqual(entry.difficulty, "Easy")
        self.assertEqual(entry.topics, ["Array", "Hash Table"])
        self.assertEqual(entry.headline, "Never Forget the One-Pass Hash Map Trick")
        self.assertEqual(entry.problem_url, "https://leetcode.com/problems/two-sum/")

    def test_entry_without_gallery_metadata_still_loads(self):
        """A manifest entry written before this field existed (e.g. the
        real state/manifest.json's pre-gallery entry) must still load."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            path.write_text(
                '{"2026-08-13:1": {"date": "2026-08-13", "problem_number": 1, '
                '"slug": "two-sum", "status": "success", "content_hash": "abc", '
                '"drive": true}}'
            )
            reloaded = load(path)

        entry = reloaded.get("2026-08-13", 1)
        self.assertIsNone(entry.title)
        self.assertEqual(entry.topics, [])

    def test_missing_file_loads_empty_manifest(self):
        manifest = load(Path("/tmp/definitely-does-not-exist-manifest.json"))
        self.assertEqual(len(manifest.entries), 0)

    def test_failed_entry_is_not_already_published(self):
        manifest = Manifest()
        manifest.record(
            ManifestEntry(
                date="2026-08-13",
                problem_number=1,
                slug="two-sum",
                status="failed",
                content_hash="abc123",
                failure_stage="verify",
                failure_reason="invalid",
            )
        )
        self.assertFalse(manifest.already_published("2026-08-13", 1))

    def test_content_hash_is_stable_regardless_of_key_order(self):
        cheatsheet = load_sample_cheatsheet_json()
        reordered = dict(reversed(list(cheatsheet.items())))
        self.assertEqual(content_hash(cheatsheet), content_hash(reordered))


if __name__ == "__main__":
    unittest.main()
