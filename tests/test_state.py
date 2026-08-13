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
