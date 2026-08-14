#!/usr/bin/env python3
"""One-shot, real (billed) smoke test for the optional OpenAI image renderer
(image_generation.provider: "openai" -- see ARCHITECTURE.md "Optional OpenAI
image renderer"). Makes exactly ONE OpenAI image-generation request per
invocation and never retries automatically beyond src/rendering/
openai_provider.py's own bounded retry on transient errors.

This is a manual, human-run verification tool, not part of the pipeline or
test suite -- tests/test_openai_renderer.py mocks the OpenAI client
entirely and spends no real credits. Never call this script from pytest,
CI, or src/main.py.

Usage (see docs/SETUP.md step 3d for how to get OPENAI_API_KEY first):

    export OPENAI_API_KEY=sk-...    # or put it in .env
    python scripts/smoke_test_openai.py
    python scripts/smoke_test_openai.py --quality high
    python scripts/smoke_test_openai.py --content-json output/2026-08-13/1/content.json

Defaults to a small built-in LeetCode #2213 fixture (the same one the
renderer's original design reference and prompts/04-live-smoke-test.md in
the handoff kit under .claude-handoff/ were built around) so it runs
standalone with no other pipeline output required. Pass --content-json to
smoke-test against a real day's schemas/cheatsheet.schema.json-shaped
content.json instead.

Inspect the printed final image path for title cropping, broken formulas,
unreadable pseudocode, content in the card's area, or card alteration
before trusting this provider for a real run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

_FIXTURE_2213 = {
    "problem": {
        "number": 2213,
        "title": "Longest Substring of One Repeating Character",
        "difficulty": "Hard",
        "topics": ["Segment Tree", "Array", "String"],
    },
    "headline": "Segment Trees Connect Runs at Matching Boundaries!",
    "problem_summary": (
        "You are given a string s and a list of query positions/characters. "
        "After each query replaces one character in s, return the longest "
        "contiguous run of one repeated character."
    ),
    "key_insight": (
        "Only runs touching the updated position can change -- a segment "
        "tree lets us recompute only the affected ancestors."
    ),
    "intuition": (
        "Each segment tracks its length, boundary characters, uniform "
        "prefix/suffix run lengths, and its best internal run. Two segments "
        "merge by checking whether the left segment's right character "
        "matches the right segment's left character."
    ),
    "approach": [
        "Build a segment tree over s; each node stores length, leftChar, rightChar, prefix, suffix, best.",
        "merge(L, R): best = max(L.best, R.best); if L.rightChar == R.leftChar, best also considers L.suffix + R.prefix.",
        "prefix/suffix extend across the boundary only when the shorter side is fully uniform and characters match.",
        "update(i, char): change one leaf, recompute all ancestors bottom-up.",
        "Answer after each update is root.best.",
    ],
    "example": {
        "input": "s = \"bbbacc\", update index 3 to 'b'",
        "states": ["bbb | acc -> best = 3", "bbb | bcc -> crossing run allowed, best = 4"],
        "output": "4",
        "explanation": "After the update, indices 0-3 are all 'b', giving a run of length 4.",
    },
    "complexity": {"time": "O(n + k log n)", "space": "O(n)"},
    "code": (
        "class Node:\n"
        "    __slots__ = ('len', 'l', 'r', 'prefix', 'suffix', 'best')\n\n"
        "def merge(L, R):\n"
        "    n = Node()\n"
        "    n.len = L.len + R.len\n"
        "    n.l, n.r = L.l, R.r\n"
        "    n.prefix, n.suffix = L.prefix, R.suffix\n"
        "    n.best = max(L.best, R.best)\n"
        "    if L.r == R.l:\n"
        "        n.best = max(n.best, L.suffix + R.prefix)\n"
        "        if L.prefix == L.len:\n"
        "            n.prefix = L.len + R.prefix\n"
        "        if R.suffix == R.len:\n"
        "            n.suffix = R.len + L.suffix\n"
        "    return n\n"
    ),
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--quality",
        default="medium",
        choices=["low", "medium", "high", "auto"],
        help="Overrides image_generation.openai.quality for this run only "
        "(default: medium -- cheaper than the high production default).",
    )
    p.add_argument(
        "--content-json",
        type=Path,
        default=None,
        help="Path to a schemas/cheatsheet.schema.json-shaped content.json "
        "(e.g. output/<date>/<problem>/content.json). Defaults to a "
        "built-in LeetCode #2213 fixture.",
    )
    p.add_argument(
        "--stage-dir",
        type=Path,
        default=REPO_ROOT / "output" / "smoke-test" / "2213",
        help="Where to write cheatsheet-openai-background.png / cheatsheet-openai-final.png.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")

    import os

    if not os.environ.get("OPENAI_API_KEY"):
        print(
            "OPENAI_API_KEY not set (checked real env vars and .env) -- see docs/SETUP.md step 3d.",
            file=sys.stderr,
        )
        return 1

    from src.config import load_settings
    from src.rendering.openai_provider import OpenAIRenderError, render

    settings = load_settings()
    settings["image_generation"]["openai"]["quality"] = args.quality

    cheatsheet = json.loads(args.content_json.read_text()) if args.content_json else _FIXTURE_2213

    card_path = REPO_ROOT / settings["contact_card"]["path"]
    before_hash = hashlib.sha256(card_path.read_bytes()).hexdigest() if card_path.exists() else None

    print(f"Making ONE real OpenAI image request (quality={args.quality})...")
    try:
        result = render(cheatsheet, settings, args.stage_dir)
    except OpenAIRenderError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1

    after_hash = hashlib.sha256(card_path.read_bytes()).hexdigest() if card_path.exists() else None

    openai_cfg = settings["image_generation"]["openai"]
    print("=== RESULT ===")
    print("provider:", result.provider)
    print("passed:", result.passed)
    print("checks:", result.checks)
    print("warnings:", result.warnings)
    print(f"dimensions: {result.width}x{result.height}")
    print("final image:", result.image_path)
    print("background:", args.stage_dir / openai_cfg["background_filename"])
    if before_hash is not None:
        print("card hash unchanged:", before_hash == after_hash)
    print()
    print(
        "Inspect the final image above for title cropping, broken formulas, "
        "unreadable pseudocode, content in the card's area, or card alteration "
        "before trusting this provider for a real run. Do not re-run this "
        "script repeatedly without a reason -- each run is one billed request."
    )
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
