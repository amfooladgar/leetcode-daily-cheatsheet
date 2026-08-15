"""Unit tests for src/main.py's _linkedin_caption() -- the pure Python-side
template that assembles the final LinkedIn caption text around the
linkedin_caption stage's model output (mirrors how _telegram_caption()
assembles the Telegram caption)."""

import unittest

from src.leetcode.models import Problem
from src.main import _linkedin_caption
from tests.helpers import load_sample_cheatsheet_json


def _problem() -> Problem:
    return Problem(
        date="2026-08-14",
        number=1,
        title="Two Sum",
        slug="two-sum",
        difficulty="Easy",
        url="https://leetcode.com/problems/two-sum/",
        statement="x",
        examples=[{"input": "nums = [2,7,11,15], target = 9", "output": "[0,1]"}],
    )


class LinkedInCaptionFormatTests(unittest.TestCase):
    def setUp(self):
        self.cheatsheet = load_sample_cheatsheet_json()
        self.problem = _problem()

    def test_includes_similar_problems_line_when_present(self):
        caption_json = {
            "solution_summary": "Use a hash map to remember seen values.",
            "similar_problems": [
                {"title": "3Sum", "reason": "same two-pointer idea"},
                {"title": "4Sum", "reason": "generalizes the same technique"},
            ],
            "hashtags": ["#leetcode", "#coding", "#arrays", "#100DaysOfCode"],
        }
        text = _linkedin_caption(self.cheatsheet, self.problem, caption_json)

        self.assertIn(self.cheatsheet["headline"], text)
        self.assertIn("Use a hash map to remember seen values.", text)
        self.assertIn(
            "Similar problems worth trying: 3Sum (same two-pointer idea); "
            "4Sum (generalizes the same technique).",
            text,
        )
        self.assertIn("LeetCode #1 Two Sum (Easy)", text)
        self.assertIn("https://leetcode.com/problems/two-sum/", text)
        self.assertIn("#leetcode #coding #arrays #100DaysOfCode", text)

    def test_omits_similar_problems_paragraph_when_empty(self):
        caption_json = {
            "solution_summary": "Use a hash map to remember seen values.",
            "similar_problems": [],
            "hashtags": ["#leetcode", "#coding", "#arrays", "#100DaysOfCode"],
        }
        text = _linkedin_caption(self.cheatsheet, self.problem, caption_json)

        self.assertNotIn("Similar problems worth trying", text)
        # No stray double-blank-line where the omitted paragraph would have been.
        self.assertNotIn("\n\n\n", text)

    def test_missing_similar_problems_key_is_treated_as_empty(self):
        caption_json = {
            "solution_summary": "Use a hash map to remember seen values.",
            "hashtags": ["#leetcode", "#coding", "#arrays", "#100DaysOfCode"],
        }
        text = _linkedin_caption(self.cheatsheet, self.problem, caption_json)
        self.assertNotIn("Similar problems worth trying", text)


if __name__ == "__main__":
    unittest.main()
