"""Minimal, dependency-free Python syntax highlighting -> HTML spans.

Same scope note as the rest of the renderer (see ARCHITECTURE.md "Diagram
component library"): the code passing through here is always a short,
schema-validated, example-tested LeetCode solution, so a line-based regex
highlighter is enough — a line that doesn't match cleanly still renders
correctly, just without color.
"""

from __future__ import annotations

import html
import keyword
import re

_TOKEN_RE = re.compile(
    r"""
    (?P<comment>\#.*$)
  | (?P<string>(\"[^\"]*\"|'[^']*'))
  | (?P<number>\b\d+\.?\d*\b)
  | (?P<name>\b[A-Za-z_][A-Za-z0-9_]*\b)
    """,
    re.VERBOSE,
)

_KEYWORDS = set(keyword.kwlist) | {"self"}

_CLASS_BY_GROUP = {
    "comment": "tok-comment",
    "string": "tok-string",
    "number": "tok-number",
}


def highlight_python_html(code: str) -> str:
    """Returns HTML: one <div class="code-line"> per source line, with
    <span class="tok-*"> wrapping keywords/strings/numbers/comments. All
    text is HTML-escaped; callers must render this with the `safe` filter
    since it IS trusted, generated HTML, not raw user input."""
    lines_html = []
    for line in code.split("\n"):
        if not line.strip():
            lines_html.append('<div class="code-line">&nbsp;</div>')
            continue

        pieces: list[str] = []
        pos = 0
        for match in _TOKEN_RE.finditer(line):
            if match.start() > pos:
                pieces.append(html.escape(line[pos : match.start()]))

            text = html.escape(match.group(0))
            if match.lastgroup == "name" and match.group(0) in _KEYWORDS:
                pieces.append(f'<span class="tok-keyword">{text}</span>')
            elif match.lastgroup in _CLASS_BY_GROUP:
                pieces.append(f'<span class="{_CLASS_BY_GROUP[match.lastgroup]}">{text}</span>')
            else:
                pieces.append(text)
            pos = match.end()

        if pos < len(line):
            pieces.append(html.escape(line[pos:]))

        lines_html.append(f'<div class="code-line">{"".join(pieces)}</div>')

    return "\n".join(lines_html)
