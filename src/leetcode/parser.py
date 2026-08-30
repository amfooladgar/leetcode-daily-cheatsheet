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

from bs4 import BeautifulSoup, NavigableString, Tag

from src.leetcode.client import RawQuestion
from src.leetcode.models import Example, Problem, SimilarQuestion

log = logging.getLogger(__name__)

_EXAMPLE_HEADING_RE = re.compile(r"^\s*example\s*\d*\s*:?\s*$", re.IGNORECASE)
_CONSTRAINTS_HEADING_RE = re.compile(r"^\s*constraints\s*:?\s*$", re.IGNORECASE)
# DOTALL + non-greedy up to the next known label: LeetCode sometimes hard-
# wraps a long array literal across a raw newline inside the <pre> block
# (e.g. "Input: nums = [2,\n10,7,...]"), and a plain '.*' (no DOTALL) stops
# at that first newline, truncating the value to "nums = [2," -- this is
# exactly what broke the 2026-08-30 run (problem 2091). The lookahead keeps
# the capture from also swallowing the next label's own line.
_INPUT_LINE_RE = re.compile(r"input\s*:\s*(.*?)(?=\n\s*output\s*:|\Z)", re.IGNORECASE | re.DOTALL)
_OUTPUT_LINE_RE = re.compile(
    r"output\s*:\s*(.*?)(?=\n\s*explanation\s*:|\Z)", re.IGNORECASE | re.DOTALL
)
# DOTALL: newer LeetCode problems put the explanation's own text in a
# separate node from the "Explanation:" label (see
# _looks_like_example_container), so it lands on a later line once the
# container is flattened to text -- the capture must be able to cross that
# line break.
_EXPLANATION_LINE_RE = re.compile(r"explanation\s*:\s*(.*)", re.IGNORECASE | re.DOTALL)

# Inline tags LeetCode may use inside the intro statement. Newer problems
# sometimes leave that statement as bare text mixed with these tags,
# directly under the document root with no wrapping <p> -- see
# _iter_top_level_blocks.
_INLINE_NODE_NAMES = frozenset(
    {
        "code",
        "strong",
        "em",
        "b",
        "i",
        "u",
        "span",
        "a",
        "sub",
        "sup",
        "br",
        "mark",
        "small",
        "font",
    }
)


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


def _iter_top_level_blocks(soup: BeautifulSoup):
    """Yields (kind, node) for each top-level block in document order.

    `kind` is the tag name for genuine block tags (p, pre, ul, ol, div).
    Bare text and inline tags (see _INLINE_NODE_NAMES) that sit directly
    under the document root -- as newer LeetCode problems sometimes leave
    the intro statement, with no wrapping <p> -- are merged into a run and
    yielded as ("text", [nodes]), so callers can treat it like a paragraph.
    """
    buffer: list = []
    for node in soup.contents:
        if isinstance(node, NavigableString):
            if node.strip():
                buffer.append(node)
            continue
        if isinstance(node, Tag) and node.name in _INLINE_NODE_NAMES:
            buffer.append(node)
            continue
        if buffer:
            yield "text", buffer
            buffer = []
        if isinstance(node, Tag):
            yield node.name, node
    if buffer:
        yield "text", buffer


_WHITESPACE_RE = re.compile(r"\s+")


def _text_from_nodes(nodes: list) -> str:
    """Concatenates nodes with no inserted separator -- unlike
    tag.get_text(" "), this doesn't split words that only look adjacent
    because they cross a tag boundary (e.g. "bcbb<u>bcba</u>" must stay
    "bcbbbcba", not "bcbb bcba"). Any real whitespace is already present in
    the source text nodes, so this just normalizes runs of it afterward."""
    raw = "".join(n.get_text() if isinstance(n, Tag) else str(n) for n in nodes)
    return _WHITESPACE_RE.sub(" ", raw).strip()


def _is_example_container(tag: Tag) -> bool:
    """The classic shape is a <pre>Input: ...\\nOutput: ...</pre> block.
    Newer LeetCode problems instead use a
    <div class="example-block"><p><strong>Input:</strong> ...</p>...</div>
    wrapper with per-field <p> tags."""
    if tag.name == "pre":
        return True
    return tag.name == "div" and "example-block" in (tag.get("class") or [])


def _example_container_text(tag: Tag) -> str:
    """Flattens an example container to newline-joined field lines for
    _parse_example_block. <pre> blocks are already preformatted text. A
    <div class="example-block"> instead holds one <p> per field (Input,
    Output, Explanation label) plus, often, the explanation's own body as
    bare text/inline tags trailing the last <p> with no wrapper -- that
    trailing run is merged with _text_from_nodes so it doesn't get torn
    apart by a naive get_text("\\n")."""
    if tag.name == "pre":
        return tag.get_text("\n", strip=True)

    lines: list[str] = []
    trailing: list = []
    for node in tag.contents:
        if isinstance(node, Tag) and node.name == "p":
            if trailing:
                text = _text_from_nodes(trailing)
                if text:
                    lines.append(text)
                trailing = []
            text = node.get_text(" ", strip=True)
            if text:
                lines.append(text)
            continue
        if isinstance(node, NavigableString):
            if node.strip():
                trailing.append(node)
            continue
        if isinstance(node, Tag):
            trailing.append(node)
    if trailing:
        text = _text_from_nodes(trailing)
        if text:
            lines.append(text)
    return "\n".join(lines)


def parse_statement_html(html: str) -> tuple[str, list[Example], list[str]]:
    """Returns (statement_text, examples, constraints)."""
    soup = BeautifulSoup(html or "", "html.parser")
    _mark_superscripts(soup)

    statement_parts: list[str] = []
    examples: list[Example] = []
    constraints: list[str] = []

    mode = "statement"
    for kind, node in _iter_top_level_blocks(soup):
        if kind == "text":
            if mode == "statement":
                text = _tighten_exponents(_text_from_nodes(node))
                if text:
                    statement_parts.append(text)
            continue

        tag = node
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
        elif mode == "examples" and _is_example_container(tag):
            example = _parse_example_block(_example_container_text(tag))
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
        similar_questions=[
            SimilarQuestion(title=sq["title"], slug=sq["titleSlug"], difficulty=sq["difficulty"])
            for sq in raw.similar_questions
        ],
    )
