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

    def output_dir_name(self) -> str:
        return f"{self.number}"
