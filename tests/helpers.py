"""Fixture loaders shared by every test module.

Plain functions rather than pytest fixtures so the whole suite runs
identically under `pytest` or `python -m unittest discover`.
"""

import json
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def load_sample_problem_html() -> str:
    return (FIXTURES / "sample_problem_content.html").read_text()


def load_sample_problem_html_v2() -> str:
    """Newer LeetCode HTML shape: intro statement as bare text/inline tags
    with no wrapping <p>, and <div class="example-block"> examples instead
    of <pre> blocks (see src/leetcode/parser.py)."""
    return (FIXTURES / "sample_problem_content_v2.html").read_text()


def load_sample_problem_json() -> dict:
    return json.loads((FIXTURES / "sample_problem.json").read_text())


def load_sample_cheatsheet_json() -> dict:
    return json.loads((FIXTURES / "sample_cheatsheet.json").read_text())


def load_sample_cheatsheet_sliding_window_json() -> dict:
    """Diagram-bearing fixture matching the comparison_states + reasoning_panel
    reference the visual design was built against."""
    return json.loads((FIXTURES / "sample_cheatsheet_sliding_window.json").read_text())


def load_sample_cheatsheet_no_diagram_json() -> dict:
    """No diagrams/reasoning_panel at all -- exercises the graceful text-only
    fallback path (see ARCHITECTURE.md 'Diagram component library')."""
    return json.loads((FIXTURES / "sample_cheatsheet_no_diagram.json").read_text())
