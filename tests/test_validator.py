import json
import unittest
from pathlib import Path

from src.claude.validator import (
    ExampleRunReport,
    ValidationError,
    run_examples,
    validate_schema,
)
from src.leetcode.models import Example
from tests.helpers import FIXTURES, load_sample_cheatsheet_json

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
            "class Solution:\n"
            "    def twoSum(self, nums, target):\n"
            "        return [0, 0]\n"
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
