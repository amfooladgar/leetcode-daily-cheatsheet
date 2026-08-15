#!/usr/bin/env python3
"""Builds the public static gallery site from state/manifest.json.

Reads every manifest entry with status="success" and drive=True (the same
definition Manifest.already_published() uses for "this was actually
published"), pairs each with its durable image copy in gallery/images/
(see src/state/gallery.py), and renders a deterministic, framework-free
HTML/CSS site into gallery/site/ -- consistent with this project's
"deterministic over AI where it matters" rendering philosophy (see
ARCHITECTURE.md "Why HTML/CSS instead of Pillow" and "Gallery site").

gallery/site/ is a build artifact: gitignored and rebuilt from scratch on
every invocation, same as output/ (see .gitignore and CLAUDE.md's
"Idempotency" rule -- rebuilding must not require re-running the pipeline).

Usage:

    python -m scripts.build_gallery
    python -m scripts.build_gallery --manifest state/manifest.json --site-dir gallery/site
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.config import load_settings
from src.state import manifest as manifest_mod

log = logging.getLogger("build_gallery")

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


@dataclass
class GalleryCard:
    number: int
    slug: str
    title: str
    difficulty: str
    topics: list[str]
    headline: str
    date: str
    problem_url: str
    image_filename: str


def _filename_stem(entry: manifest_mod.ManifestEntry, pattern: str) -> str:
    return pattern.format(number=entry.problem_number, slug=entry.slug, date=entry.date)


def collect_cards(
    manifest: manifest_mod.Manifest, images_dir: Path, filename_pattern: str
) -> list[GalleryCard]:
    """Published entries (status="success", drive=True) that have a durable
    image in `images_dir`. An entry missing its image (e.g. a pre-gallery
    manifest entry written before this feature existed) is logged and
    skipped rather than crashing the build -- a partial gallery beats a
    failed one."""
    cards: list[GalleryCard] = []
    for entry in manifest.entries.values():
        if entry.status != "success" or not entry.drive:
            continue
        stem = _filename_stem(entry, filename_pattern)
        # src/state/gallery.py::save_gallery_image() always writes the
        # QA-passed PNG under this name -- both providers' final output is
        # a .png (see config/settings.yaml image_generation.*).
        image_name = f"{stem}.png"
        if not (images_dir / image_name).exists():
            log.warning(
                "Skipping %s #%d (%s) -- no gallery image at %s",
                entry.date,
                entry.problem_number,
                entry.slug,
                images_dir / image_name,
            )
            continue
        cards.append(
            GalleryCard(
                number=entry.problem_number,
                slug=entry.slug,
                title=entry.title or entry.slug,
                difficulty=entry.difficulty or "",
                topics=sorted(entry.topics),
                headline=entry.headline or "",
                date=entry.date,
                problem_url=entry.problem_url or "",
                image_filename=image_name,
            )
        )
    cards.sort(key=lambda c: (c.date, c.number), reverse=True)
    return cards


def render_site(cards: list[GalleryCard], site_title: str, site_url: str = "") -> str:
    """`site_url`, when set (the custom-domain case -- see build_gallery's
    custom_domain docstring), lets the template emit absolute Open
    Graph/Twitter Card URLs so a shared gallery link unfurls with a real
    preview image instead of a bare title on LinkedIn/Slack/etc. Left ""
    when there's no stable domain to build an absolute URL against (the
    default *.github.io case), in which case the template omits the
    image/url OG tags rather than emit incorrect relative ones."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("gallery.html.jinja2")
    difficulties = [d for d in ["Easy", "Medium", "Hard"] if any(c.difficulty == d for c in cards)]
    topics = sorted({t for c in cards for t in c.topics})
    site_url = site_url.rstrip("/") if site_url else ""
    latest = cards[0] if cards else None
    og_image_url = f"{site_url}/images/{latest.image_filename}" if site_url and latest else ""
    og_description = (
        f"{len(cards)} auto-generated, adversarially-verified LeetCode cheat sheets "
        "— solved and checked by Claude, one per day."
    )
    return template.render(
        site_title=site_title,
        entries=cards,
        difficulties=difficulties,
        topics=topics,
        site_url=site_url,
        og_image_url=og_image_url,
        og_description=og_description,
    )


def build_gallery(
    manifest_path: Path,
    images_dir: Path,
    site_dir: Path,
    filename_pattern: str,
    site_title: str,
    custom_domain: str = "",
) -> int:
    """Returns the number of cards written. Wipes and recreates site_dir so
    a rebuild never leaves a stale image from a previous run behind.

    `custom_domain`, when set, writes a CNAME file into site_dir -- GitHub
    Pages' documented mechanism for an Actions-deployed site to keep a
    custom domain across every rebuild. The repo's Pages custom-domain
    setting alone is not enough for a workflow-driven deploy: without this
    file present in the *published artifact* each time, GitHub silently
    drops the custom domain back to the default *.github.io URL on the
    next deploy (see ARCHITECTURE.md "Gallery site")."""
    manifest = manifest_mod.load(manifest_path)
    cards = collect_cards(manifest, images_dir, filename_pattern)

    if site_dir.exists():
        shutil.rmtree(site_dir)
    site_images_dir = site_dir / "images"
    site_images_dir.mkdir(parents=True, exist_ok=True)

    for card in cards:
        shutil.copy2(images_dir / card.image_filename, site_images_dir / card.image_filename)

    html = render_site(
        cards, site_title, site_url=f"https://{custom_domain}" if custom_domain else ""
    )
    (site_dir / "index.html").write_text(html)

    if custom_domain:
        (site_dir / "CNAME").write_text(custom_domain + "\n")

    log.info("Built gallery with %d card(s) at %s", len(cards), site_dir)
    return len(cards)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build the static gallery site")
    p.add_argument("--manifest", type=Path, default=None)
    p.add_argument("--images-dir", type=Path, default=None)
    p.add_argument("--site-dir", type=Path, default=None)
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    settings = load_settings()
    manifest_path = args.manifest or REPO_ROOT / settings["state"]["manifest_path"]
    images_dir = args.images_dir or REPO_ROOT / settings["gallery"]["images_dir"]
    site_dir = args.site_dir or REPO_ROOT / settings["gallery"]["site_dir"]

    build_gallery(
        manifest_path,
        images_dir,
        site_dir,
        settings["output"]["filename_pattern"],
        settings["gallery"]["title"],
        custom_domain=settings["gallery"].get("custom_domain", ""),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
