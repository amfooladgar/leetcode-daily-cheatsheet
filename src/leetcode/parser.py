"""Normalizes a RawQuestion (raw GraphQL + HTML) into src.leetcode.models.Problem.

LeetCode's `content` field is HTML with a fairly consistent shape:

    <p>... problem statement ...</p>
    ...
    <p><strong>Example 1:</strong></p>
    <pre>Input: ...
Output: ...
Explanation: ...
</pre>
    ...
    <p><strong>Constraints:</strong></p>
    <ul><li>...</li></ul>

This is not a documented contract, so parsing here is deliberately
defensive: missing sections degrade gracefully (empty list) rather than
raising, except for the statement itself, which is required.
"""

from __future__ import annotations

import datetime as dt
import logging
import re

from bs4 import BeautifulSoup, Tag

from src.leetcode.client import RawQuestion
from src.leetcode.models import Example, Problem

log = logging.getLogger(__name__)

_EXAMPLE_HEADING_RE = re.compile(r"^\s*example\s*\d*\s*:?\s*$", re.IGNORECASE)
_CONSTRAINTS_HEADING_RE = re.compile(r"^\s*constraints\s*:?\s*$", re.IGNORECASE)
_INPUT_LINE_RE = re.compile(r"input\s*:\s*(.*)", re.IGNORECASE)
_OUTPUT_LINE_RE = re.compile(r"output\s*:\s*(.*)", re.IGNORECASE)
_EXPLANATION_LINE_RE = re.compile(r"explanation\s*:\s*(.*)", re.IGNORECASE)


def _mark_superscripts(soup: BeautifulSoup) -> None:
    """LeetCode renders exponents as <sup>, e.g. 10<sup>5</sup>. Plain
    get_text() would silently drop the '^', turning 10^5 into 105. Insert a
    literal '^' so normalized text stays numerically unambiguous. The
    surrounding get_text(separator=" ") call still needed for inline tags
    like <code> will pad this with spaces ("10 ^ 4") — _tighten_exponents()
    below cleans that back up to "10^4"."""
    for sup in soup.find_all("sup"):
        sup.insert_before("^")


def _tighten_exponents(text: str) -> str:
    """Collapses whitespace introduced around our injected '^' marker by
    get_text(separator=" "), e.g. "10 ^ 4" -> "10^4"."""
    return re.sub(r"\s*\^\s*", "^", text)


def _heading_text(tag: Tag) -> str:
    return tag.get_text(strip=True)


def _looks_like_example_heading(tag: Tag) -> bool:
    text = _heading_text(tag)
    return bool(_EXAMPLE_HEADING_RE.match(text)) and tag.find(["strong", "b"]) is not None


def _looks_like_constraints_heading(tag: Tag) -> bool:
    text = _heading_text(tag)
    return bool(_CONSTRAINTS_HEADING_RE.match(text)) and tag.find(["strong", "b"]) is not None


def _parse_example_block(pre_text: str) -> Example | None:
    input_m = _INPUT_LINE_RE.search(pre_text)
    output_m = _OUTPUT_LINE_RE.search(pre_text)
    explanation_m = _EXPLANATION_LINE_RE.search(pre_text)
    if not input_m or not output_m:
        return None
    # Explanation, if present, may span to end of block; input/output are
    # single logical values (LeetCode sometimes wraps them across lines for
    # arrays, so take everything up to the next known label).
    return Example(
        input=input_m.group(1).strip(),
        output=output_m.group(1).split("Explanation")[0].strip(),
        explanation=explanation_m.group(1).strip() if explanation_m else "",
    )


def parse_statement_html(html: str) -> tuple[str, list[Example], list[str]]:
    """Returns (statement_text, examples, constraints)."""
    soup = BeautifulSoup(html or "", "html.parser")
    _mark_superscripts(soup)

    statement_parts: list[str] = []
    examples: list[Example] = []
    constraints: list[str] = []

    mode = "statement"
    for tag in soup.find_all(["p", "pre", "ul", "ol"], recursive=False):
        if tag.name == "p" and _looks_like_example_heading(tag):
            mode = "examples"
            continue
        if tag.name == "p" and _looks_like_constraints_heading(tag):
            mode = "constraints"
            continue

        if mode == "statement" and tag.name == "p":
            text = _tighten_exponents(tag.get_text(" ", strip=True))
            if text:
                statement_parts.append(text)
        elif mode == "examples" and tag.name == "pre":
            example = _parse_example_block(tag.get_text("\n", strip=True))
            if example:
                examples.append(example)
        elif mode == "constraints" and tag.name in ("ul", "ol"):
            for li in tag.find_all("li"):
                text = _tighten_exponents(li.get_text(" ", strip=True))
                if text:
                    constraints.append(text)

    statement = "\n\n".join(statement_parts).strip()
    return statement, examples, constraints


def normalize(raw: RawQuestion) -> Problem:
    statement, examples, constraints = parse_statement_html(raw.content_html)

    if not statement:
        raise ValueError(
            f"Parsed an empty statement for '{raw.slug}' — LeetCode's HTML "
            "shape may have changed; see src/leetcode/parser.py"
        )
    if not examples:
        log.warning(
            "No examples parsed for '%s' from exampleTestcases fallback not "
            "implemented; proceeding with statement-only content.",
            raw.slug,
        )

    return Problem(
        date=dt.date.fromisoformat(raw.date) if raw.date else dt.date.today(),
        number=int(raw.frontend_id),
        title=raw.title,
        slug=raw.slug,
        difficulty=raw.difficulty,
        url=f"https://leetcode.com/problems/{raw.slug}/",
        topics=raw.topics,
        statement=statement,
        examples=examples,
        constraints=constraints,
        python_template=raw.python_template,
        is_premium=raw.is_premium,
    )
