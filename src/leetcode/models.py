"""Pydantic models for the LeetCode adapter's output.

These are the only shapes that ever leave `src/leetcode/`. Anything
LeetCode-specific (GraphQL field names, HTML structure) is resolved inside
`client.py` / `parser.py` before an object of these types is constructed.
"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field


class Example(BaseModel):
    input: str
    output: str
    explanation: str = ""


class SimilarQuestion(BaseModel):
    """One entry from LeetCode's best-effort `similarQuestions` field (see
    src/leetcode/client.py's `_parse_similar_questions`). Used to ground the
    linkedin_caption prompt's `similar_problems` output in real data instead
    of the model guessing — see ARCHITECTURE.md "Best-effort similar
    questions"."""

    title: str
    slug: str
    difficulty: str


class Problem(BaseModel):
    """A normalized LeetCode problem. Mirrors schemas/problem.schema.json —
    keep the two in sync; tests/test_schema.py asserts they match."""

    date: dt.date
    number: int
    title: str
    slug: str
    difficulty: str = Field(pattern="^(Easy|Medium|Hard)$")
    url: str = ""
    topics: list[str] = Field(default_factory=list)
    statement: str
    examples: list[Example]
    constraints: list[str] = Field(default_factory=list)
    python_template: str = ""
    is_premium: bool = False
    similar_questions: list[SimilarQuestion] = Field(default_factory=list)

    def output_dir_name(self) -> str:
        return f"{self.number}"
