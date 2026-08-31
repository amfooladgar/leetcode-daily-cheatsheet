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

    def test_linkedin_caption_schema_roundtrip(self):
        schema = json.loads((SCHEMAS_DIR / "linkedin_caption.schema.json").read_text())
        valid = {
            "solution_summary": "Use a hash map to remember seen values so each lookup is O(1).",
            "similar_problems": [{"title": "3Sum", "reason": "same two-pointer technique"}],
            "hashtags": ["#leetcode", "#coding", "#arrays", "#100DaysOfCode"],
        }
        validate_schema(valid, schema)  # should not raise

        missing_hashtags = {k: v for k, v in valid.items() if k != "hashtags"}
        with self.assertRaises(ValidationError):
            validate_schema(missing_hashtags, schema)

        too_few_hashtags = {**valid, "hashtags": ["#leetcode"]}
        with self.assertRaises(ValidationError):
            validate_schema(too_few_hashtags, schema)


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

    def test_boolean_output_using_js_literal_spelling_is_parsed(self):
        # Reproduces the 2026-08-16 "Stone Game IX" prod failure: LeetCode
        # renders boolean/null examples as `true`/`false`/`null` (JS/JSON
        # spelling), which plain ast.literal_eval rejects as a malformed
        # node -- every example failed even though the solution was correct.
        code = (
            "class Solution:\n"
            "    def stoneGameIX(self, stones):\n"
            "        return len(stones) % 2 == 1\n"
        )
        examples = [
            Example(input="stones = [1,1]", output="false"),
            Example(input="stones = [1,1,1]", output="true"),
        ]
        report = run_examples(code, examples)
        self.assertTrue(report.ok, report.failures)
        self.assertEqual(report.passed, 2)

    def test_null_output_using_js_literal_spelling_is_parsed(self):
        code = "class Solution:\n    def firstBadVersion(self, n):\n        return None\n"
        examples = [Example(input="n = 5", output="null")]
        report = run_examples(code, examples)
        self.assertTrue(report.ok, report.failures)

    def test_listnode_input_is_converted_from_array_literal(self):
        # Reproduces the 2026-08-31 "Find the Minimum and Maximum Number of
        # Nodes Between Critical Points" prod failure: LeetCode encodes a
        # ListNode example as a plain array (`head = [3,1]`), matching the
        # real judge, and the generated code only comments out its own
        # ListNode class (also matching the real judge's convention of
        # supplying it) -- every example raised AttributeError on `.next`
        # because `head` arrived as a plain list.
        code = (
            "from typing import Optional, List\n\n"
            "# class ListNode:\n"
            "#     def __init__(self, val=0, next=None):\n"
            "#         self.val = val\n"
            "#         self.next = next\n\n"
            "class Solution:\n"
            "    def nodesBetweenCriticalPoints(self, head: Optional[ListNode]) -> List[int]:\n"
            "        prev_node = head\n"
            "        curr_node = head.next\n"
            "        idx = 1\n"
            "        first_idx = -1\n"
            "        last_idx = -1\n"
            "        min_dist = float('inf')\n"
            "        while curr_node.next:\n"
            "            next_node = curr_node.next\n"
            "            is_max = curr_node.val > prev_node.val and curr_node.val > next_node.val\n"
            "            is_min = curr_node.val < prev_node.val and curr_node.val < next_node.val\n"
            "            if is_max or is_min:\n"
            "                if first_idx == -1:\n"
            "                    first_idx = idx\n"
            "                else:\n"
            "                    min_dist = min(min_dist, idx - last_idx)\n"
            "                last_idx = idx\n"
            "            prev_node, curr_node = curr_node, next_node\n"
            "            idx += 1\n"
            "        if first_idx == -1 or first_idx == last_idx:\n"
            "            return [-1, -1]\n"
            "        return [min_dist, last_idx - first_idx]\n"
        )
        examples = [
            Example(input="head = [3,1]", output="[-1,-1]"),
            Example(input="head = [5,3,1,2,5,1,2]", output="[1,3]"),
        ]
        report = run_examples(code, examples)
        self.assertTrue(report.ok, report.failures)
        self.assertEqual(report.passed, 2)

    def test_treenode_input_and_output_are_converted_from_array_literal(self):
        code = (
            "from typing import Optional\n\n"
            "# class TreeNode:\n"
            "#     def __init__(self, val=0, left=None, right=None):\n"
            "#         self.val = val\n"
            "#         self.left = left\n"
            "#         self.right = right\n\n"
            "class Solution:\n"
            "    def invertTree(self, root: Optional[TreeNode]) -> Optional[TreeNode]:\n"
            "        if root is None:\n"
            "            return None\n"
            "        root.left, root.right = root.right, root.left\n"
            "        self.invertTree(root.left)\n"
            "        self.invertTree(root.right)\n"
            "        return root\n"
        )
        examples = [Example(input="root = [4,2,7,1,3,6,9]", output="[4,7,2,9,6,3,1]")]
        report = run_examples(code, examples)
        self.assertTrue(report.ok, report.failures)
        self.assertEqual(report.passed, 1)


if __name__ == "__main__":
    unittest.main()
