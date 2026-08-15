#!/usr/bin/env python3
"""Thin CLI wrapper around src/storage/linkedin.py, used by Path B (the
manual `/post-linkedin` Claude Code command -- see
.claude/commands/post-linkedin.md and ARCHITECTURE.md "LinkedIn posting").

This script re-checks `linkedin.enabled` itself -- a second, independent
guard beyond the one .claude/commands/post-linkedin.md already performs --
so a bug or bypass in the command's own check still can't reach LinkedIn
while the config kill switch is off.

Usage:

    python -m scripts.post_to_linkedin \\
        --image output/2026-08-13/1/cheatsheet.png \\
        --caption-file /tmp/caption.txt \\
        --date 2026-08-13 \\
        --problem-number 1
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

from src.config import load_settings
from src.state import manifest as manifest_mod
from src.storage.linkedin import LinkedInPostError, post_cheatsheet

REPO_ROOT = Path(__file__).resolve().parent.parent


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Publish a rendered cheat sheet to LinkedIn")
    p.add_argument("--image", type=Path, required=True, help="Path to the cheat sheet PNG")
    p.add_argument(
        "--caption-file", type=Path, required=True, help="Path to a text file with the caption"
    )
    p.add_argument(
        "--date", type=str, required=True, help="YYYY-MM-DD, must match a manifest entry"
    )
    p.add_argument("--problem-number", type=int, required=True)
    p.add_argument(
        "--visibility",
        type=str,
        default=None,
        help="PUBLIC | CONNECTIONS (default: linkedin.visibility in config/settings.yaml)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = load_settings()

    if not settings["linkedin"]["enabled"]:
        print(
            "linkedin.enabled is false in config/settings.yaml -- refusing to post. "
            "See docs/SETUP.md step 3c.",
            file=sys.stderr,
        )
        return 1

    visibility = args.visibility or settings["linkedin"]["visibility"]
    api_version = settings["linkedin"]["api_version"]
    caption = args.caption_file.read_text()

    try:
        result = post_cheatsheet(
            image_path=args.image,
            caption=caption,
            visibility=visibility,
            api_version=api_version,
        )
    except LinkedInPostError as exc:
        print(f"LinkedIn post failed: {exc}", file=sys.stderr)
        return 1

    manifest_path = REPO_ROOT / settings["state"]["manifest_path"]
    manifest = manifest_mod.load(manifest_path)
    entry = manifest.get(args.date, args.problem_number)
    if entry is None:
        print(
            f"No manifest entry for {args.date}:{args.problem_number} -- the pipeline run for "
            "that date/problem must exist before posting to LinkedIn. The post itself "
            f"succeeded ({result.post_url}) but the manifest was not updated.",
            file=sys.stderr,
        )
        return 1

    updated_entry = dataclasses.replace(entry, linkedin=True, linkedin_post_urn=result.post_urn)
    manifest.record(updated_entry)
    manifest_mod.save(manifest, manifest_path)

    print(result.post_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
