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
from src.rendering.factory import UnknownProviderError, render_cheatsheet_with_provider
from src.state import manifest as manifest_mod
from src.storage.google_drive import DriveUploadError, upload_cheatsheet, upload_linkedin_draft
from src.storage.linkedin import LinkedInPostError
from src.storage.linkedin import post_cheatsheet as post_to_linkedin
from src.storage.telegram import (
    TelegramSendError,
    await_button_decision,
    get_update_offset,
    send_cheatsheet,
    send_linkedin_prompt,
    send_message,
)

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
    p.add_argument(
        "--dry-run", action="store_true", help="Run through rendering; skip Drive + manifest write"
    )
    p.add_argument(
        "--date", type=str, default=None, help="YYYY-MM-DD; re-run/label a specific date"
    )
    p.add_argument(
        "--problem-slug",
        type=str,
        default=None,
        help="Fetch a specific problem instead of today's daily",
    )
    p.add_argument("--force", action="store_true", help="Bypass the manifest idempotency guard")
    p.add_argument("--skip-drive", action="store_true", help="Render but never upload to Drive")
    p.add_argument(
        "--image-provider",
        choices=["existing", "openai"],
        default=None,
        help=(
            "Image-generation provider (see config/settings.yaml "
            "image_generation.provider). Overrides IMAGE_GENERATION_PROVIDER "
            "and the config file default."
        ),
    )
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def _resolve_image_provider(args: argparse.Namespace, settings: dict) -> str:
    """CLI flag > IMAGE_GENERATION_PROVIDER env var > config/settings.yaml
    default — see ARCHITECTURE.md 'Optional OpenAI image renderer'."""
    return (
        args.image_provider
        or os.environ.get("IMAGE_GENERATION_PROVIDER")
        or settings["image_generation"]["provider"]
    )


def _schedule_gate(settings: dict) -> bool:
    """Returns True if this run should actually publish. Only applies to the
    unattended daily invocation (no --date/--problem-slug/--force/
    --dry-run) — see ARCHITECTURE.md 'Daylight saving time'."""
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


def _telegram_caption(cheatsheet: dict, problem_url: str, settings: dict) -> str:
    p = cheatsheet["problem"]
    template = settings["telegram"]["caption_template"]
    return template.format(
        headline=cheatsheet["headline"],
        number=p["number"],
        title=p["title"],
        difficulty=p["difficulty"],
        url=problem_url,
    )


def _linkedin_caption(cheatsheet: dict, problem, linkedin_caption_json: dict) -> str:
    """Python-side template: the linkedin_caption stage supplies content
    fields, this function controls the final shape (mirrors
    _telegram_caption's split of concerns)."""
    blocks = [cheatsheet["headline"], linkedin_caption_json["solution_summary"]]

    similar_problems = linkedin_caption_json.get("similar_problems") or []
    if similar_problems:
        parts = "; ".join(f"{sp['title']} ({sp['reason']})" for sp in similar_problems)
        blocks.append(f"Similar problems worth trying: {parts}.")

    blocks.append(
        f"LeetCode #{problem.number} {problem.title} ({problem.difficulty})\n{problem.url}"
    )
    blocks.append(" ".join(linkedin_caption_json.get("hashtags", [])))
    return "\n\n".join(blocks)


def run(args: argparse.Namespace) -> int:
    settings = load_settings()

    # Resolve + validate the image_generation provider before spending any
    # Anthropic tokens on solve/verify/compress -- a broken openai config
    # (missing key/template/card, invalid size/quality/model) should fail
    # immediately, not after a paid Claude run (see ARCHITECTURE.md
    # "Optional OpenAI image renderer").
    image_provider = _resolve_image_provider(args, settings)
    try:
        from src.rendering.factory import validate_provider

        validate_provider(image_provider)
        if not settings["image_generation"].get("enabled", True):
            log.info(
                "image_generation.enabled=false -- using the existing renderer regardless of provider config."
            )
            image_provider = "existing"
        elif image_provider == "openai":
            from src.rendering.openai_provider import OpenAIConfigError, validate_provider_config

            try:
                validate_provider_config(settings)
            except OpenAIConfigError as exc:
                if settings["image_generation"].get("fallback_to_existing", False):
                    log.warning(
                        "Invalid OpenAI renderer configuration (%s) -- falling back to the "
                        "existing renderer (image_generation.fallback_to_existing=true).",
                        exc,
                    )
                    image_provider = "existing"
                else:
                    log.error("Invalid OpenAI renderer configuration: %s", exc)
                    return 1
    except UnknownProviderError as exc:
        log.error(str(exc))
        return 1

    # --dry-run counts as a manual invocation too: it never uploads to
    # Drive or writes the manifest (see the `if args.dry_run:` return
    # below), so there's nothing for the schedule gate to protect against.
    # Without this, docs/SETUP.md step 6's documented way to verify wiring
    # -- Actions -> "Run workflow" -> dry_run: true, no --force, no
    # --problem-slug -- silently no-ops outside schedule.target_hour and
    # produces no output files, which is exactly what it's meant to let
    # you check without waiting for tomorrow.
    manual_invocation = bool(args.date or args.problem_slug or args.force or args.dry_run)

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
        log.info(
            "%s already published (see state/manifest.json) — no-op. Use --force to redo.", date_str
        )
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
    filename_stem = settings["output"]["filename_pattern"].format(
        number=problem.number, slug=problem.slug, date=date_str
    )
    image_path = stage_dir / "cheatsheet.png"
    contact_card_path = REPO_ROOT / settings["contact_card"]["path"]

    from src.rendering.openai_provider import OpenAIRenderError

    try:
        qa = render_cheatsheet_with_provider(
            image_provider,
            cheatsheet,
            settings,
            image_path,
            stage_dir,
            contact_card_path=contact_card_path,
            fallback_to_existing=settings["image_generation"]["fallback_to_existing"],
        )
    except (UnknownProviderError, OpenAIRenderError) as exc:
        reason = f"{image_provider} renderer failed: {exc}"
        manifest.record(
            manifest_mod.ManifestEntry(
                date=date_str,
                problem_number=problem.number,
                slug=problem.slug,
                status="failed",
                content_hash=content_hash,
                failure_stage="render_openai" if image_provider == "openai" else "render_qa",
                failure_reason=reason,
                prompt_version=prompt_version,
            )
        )
        manifest_mod.save(manifest, manifest_path)
        log.error("RENDERED failed: %s", reason)
        return 1

    image_path = qa.image_path
    for warning in qa.warnings:
        log.warning(warning)
    if qa.dropped_for_overflow:
        log.warning(
            "Rendered successfully after dropping %s to fit the canvas.", qa.dropped_for_overflow
        )

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
    log.info(
        "QA_PASSED %s (%dx%d %s, provider=%s)",
        image_path,
        qa.width,
        qa.height,
        qa.format,
        qa.provider,
    )

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

    # --- TELEGRAM (independent of Drive: a Telegram failure never blocks a
    # successful Drive upload, and vice versa — see ARCHITECTURE.md
    # "Failure policy") --------------------------------------------------
    telegram_ok = False
    telegram_failed = False
    telegram_message_id = None
    if not settings["telegram"]["enabled"]:
        log.info("Skipping Telegram send (telegram.enabled=false in config/settings.yaml).")
    else:
        try:
            caption = _telegram_caption(cheatsheet, problem.url, settings)
            result = send_cheatsheet(image_path=image_path, caption=caption)
            telegram_ok = True
            telegram_message_id = result.message_id
            log.info(
                "TELEGRAM sent %s -> chat %s (message %s)",
                image_path.name,
                result.chat_id,
                result.message_id,
            )
        except TelegramSendError as exc:
            telegram_failed = True
            log.error(
                "TELEGRAM send failed (artifact is still valid at %s): %s",
                image_path,
                exc,
            )

    # --- LINKEDIN (Path A: automatic Telegram now/later prompt -- see
    # ARCHITECTURE.md "LinkedIn posting". Requires BOTH linkedin.enabled and
    # linkedin.telegram_prompt.enabled (both false by default) plus a
    # successful Telegram send above (telegram_ok), since there is no photo
    # message to reply to otherwise. This block can make the job wait up to
    # linkedin.telegram_prompt.decision_timeout_seconds for a button tap --
    # see docs/OPERATIONS.md for the tradeoff of raising it. Wrapped in a
    # broad except so a caption-drafting or Telegram-polling failure here
    # never flips a successful Drive+Telegram run's exit code -- same
    # non-blocking philosophy as Telegram relative to Drive (see
    # ARCHITECTURE.md "Failure policy"). Path B (the manual /post-linkedin
    # command) is the only other caller of post_to_linkedin(); never add a
    # third call site outside these two human-gated entry points. --------
    linkedin_ok = False
    linkedin_post_urn = None
    linkedin_draft_saved = False
    linkedin_draft_drive_file_id = None
    linkedin_cfg = settings["linkedin"]
    if telegram_ok and linkedin_cfg["enabled"] and linkedin_cfg["telegram_prompt"]["enabled"]:
        try:
            caption_result = run_stage(
                stage="linkedin_caption",
                schema_filename="generation/linkedin_caption.gen-schema.json",
                model=claude_cfg["model_compress"],
                max_turns=claude_cfg["max_turns"],
                allowed_tools=claude_cfg["allowed_tools"],
                prompt_version=prompt_version,
                problem_json=problem_json,
                cheatsheet_json=json.dumps(cheatsheet),
            )
            linkedin_caption_schema = _load_schema("linkedin_caption.schema.json")
            clamp_to_schema(caption_result.structured_output, linkedin_caption_schema)
            validate_schema(caption_result.structured_output, linkedin_caption_schema)

            caption_text = _linkedin_caption(cheatsheet, problem, caption_result.structured_output)
            (stage_dir / "linkedin_caption.txt").write_text(caption_text)

            caption_message = send_message(
                text=caption_text, reply_to_message_id=telegram_message_id
            )
            since_update_id = get_update_offset()
            prompt_message = send_linkedin_prompt(
                text="Post this to LinkedIn now, or later?",
                date=date_str,
                problem_number=problem.number,
                reply_to_message_id=caption_message.message_id,
            )
            decision = await_button_decision(
                since_update_id=since_update_id,
                date=date_str,
                problem_number=problem.number,
                timeout_seconds=linkedin_cfg["telegram_prompt"]["decision_timeout_seconds"],
                poll_interval_seconds=linkedin_cfg["telegram_prompt"]["poll_interval_seconds"],
                prompt_message_id=prompt_message.message_id,
            )

            save_draft = decision != "now"
            if decision == "now":
                try:
                    post_result = post_to_linkedin(
                        image_path=image_path,
                        caption=caption_text,
                        visibility=linkedin_cfg["visibility"],
                        api_version=linkedin_cfg["api_version"],
                    )
                    linkedin_ok = True
                    linkedin_post_urn = post_result.post_urn
                    send_message(text=f"Posted to LinkedIn: {post_result.post_url}")
                except LinkedInPostError as exc:
                    log.error("LINKEDIN post failed (falling back to saving a draft): %s", exc)
                    send_message(text=f"LinkedIn post failed: {exc}")
                    save_draft = True

            if save_draft:
                linkedin_draft_drive_file_id = upload_linkedin_draft(
                    caption_text=caption_text,
                    filename_stem=filename_stem,
                    root_folder_id=os.environ["GOOGLE_DRIVE_FOLDER_ID"],
                    category_folder_name=settings["drive"]["category_folder_name"],
                    organize_by_year_month=settings["drive"]["organize_by_year_month"],
                    year=f"{problem.date.year:04d}",
                    month=f"{problem.date.month:02d}",
                )
                linkedin_draft_saved = True
                send_message(
                    text=(
                        "Saved the caption for later — run /post-linkedin in Claude Code "
                        "anytime to review and publish it."
                    )
                )
        except Exception as exc:  # noqa: BLE001 - never fail an otherwise-successful run over this
            log.warning("LINKEDIN Path A failed (non-blocking): %s", exc)

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
            telegram=telegram_ok,
            telegram_message_id=telegram_message_id,
            linkedin=linkedin_ok,
            linkedin_post_urn=linkedin_post_urn,
            linkedin_draft_saved=linkedin_draft_saved,
            linkedin_draft_drive_file_id=linkedin_draft_drive_file_id,
            prompt_version=prompt_version,
        )
    )
    manifest_mod.save(manifest, manifest_path)

    # Exit non-zero on a genuine Drive or Telegram failure (--skip-drive and
    # telegram.enabled=false are intentional, successful partial runs — see
    # docs/OPERATIONS.md).
    return 1 if (drive_failed or telegram_failed) else 0


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
