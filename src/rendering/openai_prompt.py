"""Builds the OpenAI image-generation prompt from the same schema-validated
`cheatsheet` dict the existing renderer consumes (schemas/cheatsheet.schema.json)
-- no separate normalization layer, since that shape is already adequate.

Every dynamic value is XML-escaped before substitution into the explicit
`<problem_statement>`/`<example>`/`<code>`/etc. tags in
prompts/openai/<version>/cheatsheet.txt, so a cheat sheet whose content
happens to contain something like `</code><example>` can't spoof a tag
boundary or be mistaken for a prompt instruction (see
ARCHITECTURE.md "Optional OpenAI image renderer" -- prompt-injection
boundary).
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts" / "openai"


class PromptTemplateError(RuntimeError):
    """Raised when the configured prompt template file is missing."""


def _esc(value: str) -> str:
    return escape(str(value))


def _format_example(example: dict) -> str:
    lines = [f"Input: {example.get('input', '')}"]
    states = example.get("states") or []
    if states:
        lines.append("States: " + " -> ".join(states))
    lines.append(f"Output: {example.get('output', '')}")
    if example.get("explanation"):
        lines.append(f"Explanation: {example['explanation']}")
    return "\n".join(lines)


def _format_diagrams_summary(diagrams: list) -> str:
    if not diagrams:
        return "No structured diagram data was provided -- use only the example above for visual guidance."
    parts = []
    for diagram in diagrams:
        component = diagram.get("component", "diagram")
        title = diagram.get("title") or diagram.get("caption") or ""
        parts.append(f"{component}: {title}".strip(": "))
    return "; ".join(parts)


def load_template(prompt_version: str) -> str:
    path = PROMPTS_DIR / prompt_version / "cheatsheet.txt"
    if not path.exists():
        raise PromptTemplateError(f"No OpenAI prompt template at {path}")
    return path.read_text()


def build_prompt(
    cheatsheet: dict,
    *,
    prompt_version: str,
    card_width: int,
    card_height: int,
    card_margin_right: int,
    card_margin_bottom: int,
) -> str:
    """Renders prompts/openai/<prompt_version>/cheatsheet.txt against
    `cheatsheet` (schemas/cheatsheet.schema.json shape). Raises
    PromptTemplateError if the template file is missing."""

    template = load_template(prompt_version)
    problem = cheatsheet.get("problem", {})
    complexity = cheatsheet.get("complexity", {})

    substitutions = {
        "headline": _esc(cheatsheet.get("headline", "")),
        "problem_number": _esc(problem.get("number", "")),
        "problem_title": _esc(problem.get("title", "")),
        "problem_difficulty": _esc(problem.get("difficulty", "")),
        "problem_statement": _esc(cheatsheet.get("problem_summary", "")),
        "key_insight": _esc(cheatsheet.get("key_insight", "")),
        "intuition": _esc(cheatsheet.get("intuition", "")),
        "approach": _esc("\n".join(f"- {step}" for step in cheatsheet.get("approach", []))),
        "example": _esc(_format_example(cheatsheet.get("example", {}))),
        "diagrams_summary": _esc(_format_diagrams_summary(cheatsheet.get("diagrams") or [])),
        "code": _esc(cheatsheet.get("code", "")),
        "time_complexity": _esc(complexity.get("time", "")),
        "space_complexity": _esc(complexity.get("space", "")),
        "card_width": str(card_width),
        "card_height": str(card_height),
        "card_margin_right": str(card_margin_right),
        "card_margin_bottom": str(card_margin_bottom),
    }

    prompt = template
    for key, value in substitutions.items():
        prompt = prompt.replace(f"{{{{{key}}}}}", value)
    return prompt
