import json
import unittest
from pathlib import Path

from src.claude.validator import (
    ExampleRunReport,
    ValidationError,
    clamp_to_schema,
    run_examples,
    validate_schema,
)
from src.leetcode.models import Example
from tests.helpers import (
    FIXTURES,
    load_sample_cheatsheet_json,
    load_sample_cheatsheet_sliding_window_json,
)

SCHEMAS_DIR = Path(__file__).parent.parent / "schemas"


class ValidateSchemaTests(unittest.TestCase):
    def test_valid_cheatsheet_passes(self):
        schema = json.loads((SCHEMAS_DIR / "cheatsheet.schema.json").read_text())
        validate_schema(load_sample_cheatsheet_json(), schema)  # should not raise

    def test_missing_required_field_fails(self):
        schema = json.loads((SCHEMAS_DIR / "cheatsheet.schema.json").read_text())
        broken = load_sample_cheatsheet_json()
        del broken["code"]
        with self.assertRaises(ValidationError):
            validate_schema(broken, schema)

    def test_problem_schema_matches_fixture(self):
        schema = json.loads((SCHEMAS_DIR / "problem.schema.json").read_text())
        problem = json.loads((FIXTURES / "sample_problem.json").read_text())
        validate_schema(problem, schema)  # should not raise


class ClampToSchemaTests(unittest.TestCase):
    def setUp(self):
        self.schema = json.loads((SCHEMAS_DIR / "cheatsheet.schema.json").read_text())

    def test_leaves_already_valid_data_untouched(self):
        cheatsheet = load_sample_cheatsheet_json()
        before = json.loads(json.dumps(cheatsheet))
        clamped = clamp_to_schema(cheatsheet, self.schema)
        self.assertEqual(clamped, 0)
        self.assertEqual(cheatsheet, before)

    def test_clamps_top_level_over_length_field(self):
        cheatsheet = load_sample_cheatsheet_json()
        cheatsheet["headline"] = "x" * 200  # over the 90-char maxLength
        clamped = clamp_to_schema(cheatsheet, self.schema)
        self.assertEqual(clamped, 1)
        self.assertLessEqual(len(cheatsheet["headline"]), 90)
        self.assertTrue(cheatsheet["headline"].endswith("…"))
        validate_schema(cheatsheet, self.schema)  # now passes

    def test_clamps_reasoning_panel_bullet_reproducing_real_failure(self):
        cheatsheet = load_sample_cheatsheet_sliding_window_json()
        overlong = (
            "Every index earlier than the current one has already been inserted "
            "into the map, so no valid earlier partner is ever missed."
        )
        self.assertGreater(len(overlong), 120)
        cheatsheet.setdefault("reasoning_panel", {"title": "Why it works", "bullets": []})
        cheatsheet["reasoning_panel"]["bullets"] = [overlong]
        with self.assertRaises(ValidationError):
            validate_schema(cheatsheet, self.schema)  # confirms it fails before clamping

        clamped = clamp_to_schema(cheatsheet, self.schema)
        self.assertEqual(clamped, 1)
        self.assertLessEqual(len(cheatsheet["reasoning_panel"]["bullets"][0]), 120)
        validate_schema(cheatsheet, self.schema)  # should not raise

    def test_clamps_inside_oneof_diagram_via_component_discriminator(self):
        cheatsheet = load_sample_cheatsheet_sliding_window_json()
        cheatsheet["diagrams"][0]["cases"][0]["label"] = "y" * 100  # over the 60-char maxLength
        clamped = clamp_to_schema(cheatsheet, self.schema)
        self.assertGreaterEqual(clamped, 1)
        self.assertLessEqual(len(cheatsheet["diagrams"][0]["cases"][0]["label"]), 60)
        validate_schema(cheatsheet, self.schema)  # should not raise

    def test_does_not_touch_non_length_violations(self):
        cheatsheet = load_sample_cheatsheet_json()
        cheatsheet["problem"]["difficulty"] = "Extreme"  # invalid enum value, not a length issue
        clamp_to_schema(cheatsheet, self.schema)
        self.assertEqual(cheatsheet["problem"]["difficulty"], "Extreme")
        with self.assertRaises(ValidationError):
            validate_schema(cheatsheet, self.schema)


class RunExamplesTests(unittest.TestCase):
    def test_correct_solution_passes_all_examples(self):
        cheatsheet = load_sample_cheatsheet_json()
        examples = [
            Example(input="nums = [2,7,11,15], target = 9", output="[0, 1]"),
            Example(input="nums = [3,2,4], target = 6", output="[1, 2]"),
        ]
        report = run_examples(cheatsheet["code"], examples)
        self.assertIsInstance(report, ExampleRunReport)
        self.assertTrue(report.ok, report.failures)
        self.assertEqual(report.passed, 2)

    def test_incorrect_solution_is_caught(self):
        broken_code = (
            "class Solution:\n    def twoSum(self, nums, target):\n        return [0, 0]\n"
        )
        examples = [Example(input="nums = [2,7,11,15], target = 9", output="[0, 1]")]
        report = run_examples(broken_code, examples)
        self.assertFalse(report.ok)
        self.assertEqual(len(report.failures), 1)

    def test_infinite_loop_is_caught_by_timeout(self):
        bad_code = (
            "class Solution:\n"
            "    def twoSum(self, nums, target):\n"
            "        while True:\n"
            "            pass\n"
        )
        examples = [Example(input="nums = [2,7,11,15], target = 9", output="[0, 1]")]
        report = run_examples(bad_code, examples, timeout_seconds=1)
        self.assertFalse(report.ok)
        self.assertIn("exceeded", report.failures[0])


if __name__ == "__main__":
    unittest.main()
