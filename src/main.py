"""Pipeline orchestrator. Deliberately thin: each stage's real logic lives
in its own module (src/leetcode, src/claude, src/rendering, src/storage,
src/state) so it's independently testable — see CLAUDE.md "Code quality".

Stage sequence (see ARCHITECTURE.md for the full diagram and failure
policy): FETCHED -> NORMALIZED -> SOLVED -> VERIFIED -> TESTED -> COMPRESSED
-> RENDERED -> QA_PASSED -> UPLOADED -> manifest written.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from src.claude.runner import ClaudeStageError, run_stage
from src.claude.validator import (
    ExampleExecutionError,
    ValidationError,
    clamp_to_schema,
    run_examples,
    validate_schema,
)
from src.config import load_settings
from src.leetcode.client import LeetCodeClient, LeetCodeError, PremiumProblemError
from src.leetcode.parser import normalize
from src.state import manifest as manifest_mod
from src.storage.google_drive import DriveUploadError, upload_cheatsheet

log = logging.getLogger("cheatsheet")

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = REPO_ROOT / "schemas"


class PipelineFailure(RuntimeError):
    def __init__(self, stage: str, reason: str):
        super().__init__(f"[{stage}] {reason}")
        self.stage = stage
        self.reason = reason


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="LeetCode daily cheat-sheet pipeline")
    p.add_argument("--dry-run", action="store_true", help="Run through rendering; skip Drive + manifest write")
    p.add_argument("--date", type=str, default=None, help="YYYY-MM-DD; re-run/label a specific date")
    p.add_argument("--problem-slug", type=str, default=None, help="Fetch a specific problem instead of today's daily")
    p.add_argument("--force", action="store_true", help="Bypass the manifest idempotency guard")
    p.add_argument("--skip-drive", action="store_true", help="Render but never upload to Drive")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def _schedule_gate(settings: dict) -> bool:
    """Returns True if this run should actually publish. Only applies to the
    unattended daily invocation (no --date/--problem-slug/--force) — see
    ARCHITECTURE.md 'Daylight saving time'."""
    tz = ZoneInfo(settings["schedule"]["timezone"])
    now = dt.datetime.now(tz)
    target_hour = settings["schedule"]["target_hour"]
    if now.hour != target_hour:
        log.info(
            "Current %s time is %02d:%02d, not the configured target hour %02d:00 — "
            "no-op (this is expected for the cron leg that isn't currently DST-correct).",
            settings["schedule"]["timezone"],
            now.hour,
            now.minute,
            target_hour,
        )
        return False
    return True


def _load_schema(filename: str) -> dict:
    return json.loads((SCHEMAS_DIR / filename).read_text())


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def _markdown_summary(cheatsheet: dict, problem_url: str) -> str:
    p = cheatsheet["problem"]
    lines = [
        f"# #{p['number']} {p['title']} ({p['difficulty']})",
        "",
        f"**{cheatsheet['headline']}**",
        "",
        f"Source: {problem_url}",
        "",
        "## Problem",
        cheatsheet.get("problem_summary", ""),
        "",
        "## Intuition",
        cheatsheet["intuition"],
        "",
        "## Approach",
        *[f"{i}. {step}" for i, step in enumerate(cheatsheet["approach"], start=1)],
        "",
        "## Complexity",
        f"- Time: {cheatsheet['complexity']['time']}",
        f"- Space: {cheatsheet['complexity']['space']}",
        "",
        "## Code",
        "```python",
        cheatsheet["code"],
        "```",
        "",
    ]
    return "\n".join(lines)


def run(args: argparse.Namespace) -> int:
    settings = load_settings()
    manual_invocation = bool(args.date or args.problem_slug or args.force)

    if not manual_invocation and not _schedule_gate(settings):
        return 0

    output_root = REPO_ROOT / settings["output"]["local_dir"]
    manifest_path = REPO_ROOT / settings["state"]["manifest_path"]
    manifest = manifest_mod.load(manifest_path)

    # --- FETCHED + NORMALIZED ------------------------------------------
    client = LeetCodeClient(
        timeout_seconds=settings["leetcode"]["request_timeout_seconds"],
        max_retries=settings["leetcode"]["max_retries"],
    )
    try:
        if args.problem_slug:
            date_label = args.date or dt.date.today().isoformat()
            raw = client.fetch_by_slug(args.problem_slug, date=date_label)
        else:
            raw = client.fetch_daily()
        problem = normalize(raw)
    except PremiumProblemError as exc:
        log.error("Premium problem, skipping per leetcode.premium_policy: %s", exc)
        return 0
    except (LeetCodeError, ValueError) as exc:
        log.error("FETCHED/NORMALIZED failed: %s", exc)
        return 1

    if args.date:
        problem.date = dt.date.fromisoformat(args.date)
    date_str = problem.date.isoformat()

    stage_dir = output_root / date_str / str(problem.number)
    _write_json(stage_dir / "problem.json", json.loads(problem.model_dump_json()))
    log.info("NORMALIZED #%d %s (%s)", problem.number, problem.title, problem.difficulty)

    if not args.force and manifest.already_published(date_str, problem.number):
        log.info("%s already published (see state/manifest.json) — no-op. Use --force to redo.", date_str)
        return 0

    claude_cfg = settings["claude"]
    prompt_version = "v1"
    problem_json = problem.model_dump_json(indent=2)

    def _solve() -> dict:
        result = run_stage(
            stage="solve",
            # The CLI-facing schema is a simplified twin (no $ref/oneOf) --
            # see schemas/generation/solve.gen-schema.json's description and
            # ARCHITECTURE.md "Why two schema files per stage". The full,
            # precise shape is still enforced below via validate_schema().
            schema_filename="generation/solve.gen-schema.json",
            model=claude_cfg["model_solve"],
            max_turns=claude_cfg["max_turns"],
            allowed_tools=claude_cfg["allowed_tools"],
            prompt_version=prompt_version,
            problem_json=problem_json,
        )
        solve_schema = _load_schema("solve.schema.json")
        clamp_to_schema(result.structured_output, solve_schema)
        validate_schema(result.structured_output, solve_schema)
        return result.structured_output

    def _verify(solve_json: dict) -> dict:
        result = run_stage(
            stage="verify",
            schema_filename="verify.schema.json",
            model=claude_cfg["model_verify"],
            max_turns=claude_cfg["max_turns"],
            allowed_tools=claude_cfg["allowed_tools"],
            prompt_version=prompt_version,
            problem_json=problem_json,
            solve_json=json.dumps(solve_json),
        )
        verify_schema = _load_schema("verify.schema.json")
        clamp_to_schema(result.structured_output, verify_schema)
        validate_schema(result.structured_output, verify_schema)
        return result.structured_output

    # --- SOLVED + VERIFIED (one regeneration attempt on invalid) --------
    try:
        solved = _solve()
        _write_json(stage_dir / "solve.json", solved)

        verified = _verify(solved)
        _write_json(stage_dir / "verify.json", verified)

        if not verified["valid"]:
            log.warning("Verification failed, regenerating once: %s", verified["issues"])
            solved = _solve()
            _write_json(stage_dir / "solve.json", solved)
            verified = _verify(solved)
            _write_json(stage_dir / "verify.json", verified)

        if not verified["valid"]:
            reason = f"still invalid after regeneration: {verified['issues']}"
            manifest.record(
                manifest_mod.ManifestEntry(
                    date=date_str,
                    problem_number=problem.number,
                    slug=problem.slug,
                    status="failed",
                    content_hash="",
                    failure_stage="verify",
                    failure_reason=reason,
                    prompt_version=prompt_version,
                )
            )
            manifest_mod.save(manifest, manifest_path)
            log.error("VERIFIED failed: %s", reason)
            return 1
    except (ClaudeStageError, ValidationError) as exc:
        log.error("SOLVED/VERIFIED failed: %s", exc)
        return 1

    if verified.get("corrected_code"):
        solved["code"] = verified["corrected_code"]
    solved["complexity"]["time"] = verified["time_complexity"]
    solved["complexity"]["space"] = verified["space_complexity"]

    # --- TESTED ----------------------------------------------------------
    try:
        report = run_examples(solved["code"], problem.examples)
    except ExampleExecutionError as exc:
        log.error("TESTED failed to execute: %s", exc)
        return 1

    for skip_reason in report.skipped:
        log.warning("Example test skipped: %s", skip_reason)
    if not report.ok:
        reason = "; ".join(report.failures)
        manifest.record(
            manifest_mod.ManifestEntry(
                date=date_str,
                problem_number=problem.number,
                slug=problem.slug,
                status="failed",
                content_hash="",
                failure_stage="tested",
                failure_reason=reason,
                prompt_version=prompt_version,
            )
        )
        manifest_mod.save(manifest, manifest_path)
        log.error("TESTED failed: %s", reason)
        return 1
    log.info("TESTED %d/%d official examples passed", report.passed, report.total)

    # --- COMPRESSED --------------------------------------------------------
    content_cfg = settings["content"]
    try:
        compress_result = run_stage(
            stage="compress",
            # See the matching comment on the solve() call above -- the CLI
            # gets the simplified generation schema; validate_schema() below
            # still enforces the full schemas/cheatsheet.schema.json shape.
            schema_filename="generation/cheatsheet.gen-schema.json",
            model=claude_cfg["model_compress"],
            max_turns=claude_cfg["max_turns"],
            allowed_tools=claude_cfg["allowed_tools"],
            prompt_version=prompt_version,
            problem_json=problem_json,
            verified_solve_json=json.dumps(solved),
            headline_max_words=str(content_cfg["headline_max_words"]),
            problem_summary_max_words=str(content_cfg["problem_summary_max_words"]),
            intuition_max_words=str(content_cfg["intuition_max_words"]),
            approach_max_steps=str(content_cfg["approach_max_steps"]),
            approach_max_words_per_step=str(content_cfg["approach_max_words_per_step"]),
            example_max_states=str(content_cfg["example_max_states"]),
            code_preferred_max_lines=str(content_cfg["code_preferred_max_lines"]),
            code_absolute_max_lines=str(content_cfg["code_absolute_max_lines"]),
        )
        cheatsheet = compress_result.structured_output
        cheatsheet_schema = _load_schema("cheatsheet.schema.json")
        clamp_to_schema(cheatsheet, cheatsheet_schema)
        validate_schema(cheatsheet, cheatsheet_schema)
    except (ClaudeStageError, ValidationError) as exc:
        log.error("COMPRESSED failed: %s", exc)
        return 1

    cheatsheet.setdefault("prompt_version", prompt_version)
    content_hash = manifest_mod.content_hash(cheatsheet)
    _write_json(stage_dir / "content.json", cheatsheet)

    # --- RENDERED + QA_PASSED --------------------------------------------
    from src.rendering.render import render_cheatsheet  # deferred: needs Playwright

    filename_stem = settings["output"]["filename_pattern"].format(
        number=problem.number, slug=problem.slug, date=date_str
    )
    image_path = stage_dir / "cheatsheet.png"
    contact_card_path = REPO_ROOT / settings["contact_card"]["path"]

    qa = render_cheatsheet(cheatsheet, settings, image_path, contact_card_path=contact_card_path)
    for warning in qa.warnings:
        log.warning(warning)

    if not qa.passed:
        reason = f"QA gate failed: {qa.failed_checks}"
        manifest.record(
            manifest_mod.ManifestEntry(
                date=date_str,
                problem_number=problem.number,
                slug=problem.slug,
                status="failed",
                content_hash=content_hash,
                failure_stage="render_qa",
                failure_reason=reason,
                prompt_version=prompt_version,
            )
        )
        manifest_mod.save(manifest, manifest_path)
        log.error("RENDERED failed: %s", reason)
        return 1
    log.info("QA_PASSED %s (%dx%d %s)", image_path, qa.width, qa.height, qa.format)

    markdown_path = None
    if settings["drive"]["upload_markdown_summary"]:
        markdown_path = stage_dir / f"{filename_stem}.md"
        markdown_path.write_text(_markdown_summary(cheatsheet, problem.url))

    if args.dry_run:
        log.info("Skipping Drive upload and manifest write (--dry-run).")
        return 0

    drive_ok = False
    drive_failed = False
    drive_file_id = None
    if args.skip_drive:
        log.info("Skipping Drive upload (--skip-drive); manifest will record drive=false.")
    else:
        try:
            result = upload_cheatsheet(
                image_path=image_path,
                markdown_path=markdown_path,
                filename_stem=filename_stem,
                root_folder_id=os.environ["GOOGLE_DRIVE_FOLDER_ID"],
                category_folder_name=settings["drive"]["category_folder_name"],
                organize_by_year_month=settings["drive"]["organize_by_year_month"],
                year=f"{problem.date.year:04d}",
                month=f"{problem.date.month:02d}",
            )
            drive_ok = True
            drive_file_id = result.image_file_id
            log.info("UPLOADED %s -> Drive file %s", image_path.name, drive_file_id)
        except (DriveUploadError, KeyError) as exc:
            drive_failed = True
            log.error(
                "UPLOADED failed (artifact is still valid at %s, see docs/OPERATIONS.md "
                "'When the Drive upload fails'): %s",
                image_path,
                exc,
            )

    manifest.record(
        manifest_mod.ManifestEntry(
            date=date_str,
            problem_number=problem.number,
            slug=problem.slug,
            status="success",
            content_hash=content_hash,
            image_filename=image_path.name,
            drive=drive_ok,
            drive_file_id=drive_file_id,
            prompt_version=prompt_version,
        )
    )
    manifest_mod.save(manifest, manifest_path)

    # Exit non-zero only on a genuine Drive failure (--skip-drive is an
    # intentional, successful partial run — see docs/OPERATIONS.md).
    return 1 if drive_failed else 0


def main(argv: list[str] | None = None) -> int:
    # Local dev only: populates os.environ from .env at the repo root (see
    # README.md's "cp .env.example .env" step) so ANTHROPIC_API_KEY /
    # GOOGLE_OAUTH_* / GOOGLE_DRIVE_FOLDER_ID work without manually
    # `export`-ing each one. `override=False` (python-dotenv's default)
    # means a real env var -- e.g. a GitHub Actions secret in CI -- always
    # wins over anything in .env, so this is a no-op in production. A
    # missing .env (e.g. in CI, which doesn't have one) is also a silent
    # no-op, not an error.
    load_dotenv(REPO_ROOT / ".env")

    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    try:
        return run(args)
    except PipelineFailure as exc:
        log.error(str(exc))
        return 1
    except Exception:
        # Last-resort safety net so an unanticipated bug (e.g. a malformed
        # config/settings.yaml, a missing environment variable we didn't
        # explicitly check) exits cleanly with a logged traceback rather
        # than a bare crash — this is what a GitHub Actions log should show
        # you first when triaging a red run (see docs/OPERATIONS.md).
        log.exception("Unhandled error in pipeline")
        return 1


if __name__ == "__main__":
    sys.exit(main())
