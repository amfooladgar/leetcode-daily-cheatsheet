import unittest
from unittest import mock

from src.leetcode.client import LeetCodeClient, PremiumProblemError, RawQuestion
from src.leetcode.parser import _parse_example_block, normalize, parse_statement_html
from tests.helpers import load_sample_problem_html, load_sample_problem_html_v2


class ParseStatementHtmlTests(unittest.TestCase):
    def setUp(self):
        self.html = load_sample_problem_html()

    def test_extracts_statement_examples_constraints(self):
        statement, examples, constraints = parse_statement_html(self.html)

        self.assertIn("integer array", statement)
        self.assertEqual(len(examples), 2)
        self.assertEqual(examples[0].input, "nums = [2,7,11,15], target = 9")
        self.assertEqual(examples[0].output, "[0,1]")
        self.assertIn("nums[0] + nums[1]", examples[0].explanation)
        self.assertEqual(len(constraints), 3)
        # superscript exponents must survive as '^', not silently vanish
        self.assertIn("10^4", constraints[0])


class ParseExampleBlockLineWrapTests(unittest.TestCase):
    """LeetCode sometimes hard-wraps a long array literal across a raw
    newline inside the <pre> block (e.g. problem 2091 on 2026-08-30, which
    truncated to input="nums = [2," and broke the pipeline). The input/
    output regexes must span that line break instead of stopping at it."""

    def test_input_spans_wrapped_array_literal(self):
        pre_text = (
            "Input: nums = [2,\n10,7,5,4,1,8,9]\n"
            "Output: 5\n"
            "Explanation: the min is at index 5 and the max is at index 1."
        )
        example = _parse_example_block(pre_text)

        self.assertIsNotNone(example)
        self.assertEqual(example.input, "nums = [2,\n10,7,5,4,1,8,9]")
        self.assertEqual(example.output, "5")
        self.assertIn("the min is at index 5", example.explanation)

    def test_output_spans_wrapped_array_literal(self):
        pre_text = "Input: nums = [1,2]\nOutput: [0,\n1]\nExplanation: trivial."
        example = _parse_example_block(pre_text)

        self.assertIsNotNone(example)
        self.assertEqual(example.output, "[0,\n1]")


class ParseStatementHtmlV2Tests(unittest.TestCase):
    """Newer LeetCode shape: bare-text intro statement (no wrapping <p>)
    plus <div class="example-block"> examples instead of <pre> blocks."""

    def setUp(self):
        self.html = load_sample_problem_html_v2()

    def test_extracts_statement_examples_constraints(self):
        statement, examples, constraints = parse_statement_html(self.html)

        self.assertIn("maximum", statement)
        self.assertIn("substring", statement)
        self.assertEqual(len(examples), 2)
        self.assertEqual(examples[0].input, 's = "bcbbbcba"')
        self.assertEqual(examples[0].output, "4")
        self.assertIn("bcbbbcba", examples[0].explanation)
        self.assertEqual(len(constraints), 2)
        self.assertIn("10^2", constraints[0])


class NormalizeTests(unittest.TestCase):
    def test_builds_valid_problem(self):
        raw = RawQuestion(
            date="2026-08-13",
            frontend_id="1",
            title="Two Sum",
            slug="two-sum",
            difficulty="Easy",
            content_html=load_sample_problem_html(),
            topics=["Array", "Hash Table"],
            example_testcases="",
            python_template="class Solution: ...",
            is_premium=False,
        )
        problem = normalize(raw)
        self.assertEqual(problem.number, 1)
        self.assertEqual(problem.slug, "two-sum")
        self.assertEqual(len(problem.examples), 2)

    def test_raises_on_empty_statement(self):
        raw = RawQuestion(
            date="2026-08-13",
            frontend_id="2",
            title="Empty",
            slug="empty",
            difficulty="Easy",
            content_html="<p></p>",
            topics=[],
            example_testcases="",
            python_template="",
            is_premium=False,
        )
        with self.assertRaises(ValueError):
            normalize(raw)


def _mock_response(json_body: dict, status_code: int = 200) -> mock.Mock:
    resp = mock.Mock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    resp.raise_for_status.return_value = None
    return resp


class LeetCodeClientTests(unittest.TestCase):
    def test_fetch_daily_slug_parses_graphql_response(self):
        client = LeetCodeClient()
        payload = {
            "data": {
                "activeDailyCodingChallengeQuestion": {
                    "date": "2026-08-13",
                    "link": "/problems/two-sum/",
                    "question": {
                        "questionFrontendId": "1",
                        "title": "Two Sum",
                        "titleSlug": "two-sum",
                        "difficulty": "Easy",
                    },
                }
            }
        }
        with mock.patch.object(client._session, "post", return_value=_mock_response(payload)):
            date, slug = client.fetch_daily_slug()
        self.assertEqual(date, "2026-08-13")
        self.assertEqual(slug, "two-sum")

    def test_fetch_question_raises_on_premium(self):
        client = LeetCodeClient()
        payload = {
            "data": {
                "question": {
                    "questionFrontendId": "999",
                    "title": "Premium Problem",
                    "titleSlug": "premium-problem",
                    "content": load_sample_problem_html(),
                    "difficulty": "Hard",
                    "exampleTestcases": "",
                    "topicTags": [],
                    "codeSnippets": [],
                    "isPaidOnly": True,
                }
            }
        }
        with (
            mock.patch.object(client._session, "post", return_value=_mock_response(payload)),
            self.assertRaises(PremiumProblemError),
        ):
            client.fetch_question("premium-problem")


if __name__ == "__main__":
    unittest.main()
