# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Fixed
- A real (non-dry-run) run on a Hard problem (#2213, "Longest Substring of
  One Repeating Character") got all the way through solve/verify/compress
  and then failed at the render step: `Content overflowed the canvas by
  ~184px` -> `RENDERED failed: QA gate failed: ['no_overflow']`, exit 1,
  nothing published. The compressed content was otherwise valid -- it was
  just a few dozen pixels too tall for the fixed 1080x1350 canvas, most
  likely because of the optional `reasoning_panel` / `diagrams` sections
  (see the "Diagram component library" note in ARCHITECTURE.md: a dropped
  diagram is already treated as an acceptable outcome, just not one the
  renderer previously acted on automatically). Re-invoking `compress` to
  ask Claude to shrink it further would cost another paid API call for a
  purely mechanical problem. Added `_render_with_overflow_recovery()` in
  `src/main.py`: after a render whose *only* failed QA check is
  `no_overflow`, it drops `reasoning_panel`, then `diagrams`, one at a
  time, and re-renders after each -- free and deterministic, no extra
  Claude calls -- stopping as soon as it fits or nothing droppable is
  left. Any other failed check (wrong dimensions, wrong format, missing
  headline/code) is left alone and fails exactly as before. Added
  `tests/test_overflow_recovery.py` (4 tests covering: drops
  `reasoning_panel` and it fits; still overflows so `diagrams` is dropped
  too; gives up cleanly with nothing left to drop; leaves a non-overflow
  failure untouched) -- confirmed two of them fail against a deliberately
  broken drop condition before confirming they pass against the real fix.
- CI turned green, but manually triggering `daily.yml` via Actions ->
  "Run workflow" (following docs/SETUP.md step 6: `dry_run: true`, leaving
  `--force` and `--problem-slug` unset) produced no output files. The run
  logged `Current America/New_York time is 08:35, not the configured
  target hour 09:00 -- no-op` and exited 0. `manual_invocation` in
  `src/main.py`'s `run()` only checked `args.date`/`args.problem_slug`/
  `args.force` -- not `args.dry_run` -- so a plain dry run fell through to
  `_schedule_gate()` and was silently skipped outside `target_hour`.
  `--dry-run` never uploads to Drive or writes the manifest on its own, so
  gating it by time of day protected nothing while defeating the exact
  verification flow the docs point people at. Added `args.dry_run` to
  `manual_invocation`; added a regression test that reproduces the bug
  (confirmed it fails without the fix, passes with it) by mocking
  `_schedule_gate` to return `False` and asserting `--dry-run` alone still
  runs the pipeline.
- After pinning `channel="chromium"`, CI failed *again* at the exact same
  spot, this time with `Executable doesn't exist at .../chromium-<rev>/
  chrome-linux64/chrome` -- the regular chromium build, not the shell
  variant this time. `.github/workflows/ci.yml`'s `playwright install
  --with-deps chromium` step never reported a failure either time, which
  matches a documented Playwright bug (microsoft/playwright#36412): the
  install command can exit 0 without leaving a working browser behind
  (silent download corruption in CI). Rather than keep chasing which
  specific binary goes missing on which run, added
  `scripts/verify_playwright_chromium.py` -- launches Chromium right after
  the install step, in its own CI step, and retries once via `playwright
  install --force` before failing loudly with a clear message instead of
  a 10-test-deep pytest wall. Wired into both `ci.yml` and `daily.yml`
  (the actual production run has the same exposure, and a silent failure
  there means a missed day, not just a red check). Verified locally by
  hiding both the chromium and chromium-headless-shell binaries and
  confirming the script detects, retries, and fails clearly.
- With CI able to collect tests again, `pytest -q` then failed 10 tests
  with `BrowserType.launch: Executable doesn't exist at .../chromium_
  headless_shell-*/chrome-headless-shell` -- `pw.chromium.launch()` with no
  `channel` silently prefers the separate "chromium-headless-shell" binary
  for headless runs (Playwright's default since v1.45), which some
  Playwright versions don't actually install even when `playwright install
  chromium` (or `--with-deps chromium`, as `ci.yml` runs) was run, while
  the regular chromium build sits there installed and unused. Reproduced
  by hiding the headless-shell binary locally with the full chromium build
  still present. `src/rendering/render.py` now passes `channel="chromium"`
  to pin the full build, sidestepping the whole headless-shell
  install-availability question. Added a mocked regression test
  (`test_launches_with_channel_chromium_not_default_headless_shell`)
  asserting `launch()` is always called with `channel="chromium"`.
- `.github/workflows/ci.yml`'s `pytest -q` step failed every test module
  with `ModuleNotFoundError: No module named 'src'`, even though the exact
  same suite passes locally via `python -m unittest discover -s tests` (and
  `python -m pytest -q`). Root cause: `python -m <tool>` prepends the
  current directory to `sys.path`, but invoking the `pytest` console script
  directly (as CI does) does not -- so the repo root was never importable
  as a package source, only in the invocation style nobody was testing.
  Added `pythonpath = ["."]` to `pyproject.toml`'s `[tool.pytest.ini_options]`
  (a native pytest>=7 ini option, no extra dependency) so every invocation
  style — bare `pytest`, `python -m pytest`, an IDE's test runner — behaves
  identically.
- A real Drive upload failed with `GOOGLE_OAUTH_REFRESH_TOKEN not set` even
  after the value was added to `.env` -- nothing in the codebase actually
  loaded `.env` into `os.environ`; `README.md`'s `cp .env.example .env`
  step implied it worked, but only a manually-`export`ed var ever did.
  Added `python-dotenv` to `requirements.txt` and call `load_dotenv(REPO_ROOT
  / ".env")` at the top of `src/main.py`'s `main()` and
  `scripts/authorize_google_drive.py`'s `main()`. `override=False`
  (python-dotenv's default) means a real exported env var -- e.g. a GitHub
  Actions secret in CI -- always wins over `.env`, and a missing `.env` is
  a silent no-op, so this changes nothing about the CI path. See
  docs/SETUP.md step 3.7.
- A real `solve` run failed schema validation with `reasoning_panel ->
  bullets -> 1: '...' is too long` (124 chars against a 120 `maxLength`) --
  Claude has no tool access in these stages and can't precisely count
  characters against a hard limit, so occasional small overshoot is
  expected, not a reasoning bug. Added `clamp_to_schema()` to
  `src/claude/validator.py`, run on solve/verify/compress output right
  before `validate_schema()`: it walks the same `$ref`/`$defs`/`oneOf`
  structure as the real schema and truncates any over-length string
  in place (logging a warning) instead of failing the whole (paid) run.
  Every other constraint (`enum`, `const`, `required`, `minLength`,
  item-count bounds) is untouched and still fails validation normally.
  See ARCHITECTURE.md "Why the prompts explicitly say 'no tool access'".
- A real (non-dry-run) publish failed at the Drive upload step with `403
  storageQuotaExceeded: "Service Accounts do not have storage quota."` --
  a fundamental limitation, not a permissions/sharing bug: service
  accounts can create folders but cannot upload file content into a
  personal (non-Workspace) Drive folder, no matter how they're shared.
  Replaced the service-account auth in `src/storage/google_drive.py` with
  OAuth 2.0 user credentials (authenticating as the actual Google account
  that owns the target folder), via a new one-time local script
  `scripts/authorize_google_drive.py` that prints a non-expiring refresh
  token. `GOOGLE_SERVICE_ACCOUNT_JSON` is replaced by
  `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` /
  `GOOGLE_OAUTH_REFRESH_TOKEN` everywhere (`.env.example`, `daily.yml`,
  docs/SETUP.md step 3). See ARCHITECTURE.md "Why OAuth instead of a
  service account".
- `render_cheatsheet()` crashed with a raw Playwright traceback
  (`BrowserType.launch: Executable doesn't exist ...`) when Chromium's
  browser binary hadn't been downloaded yet -- a one-time step separate
  from `pip install -r requirements.txt` that's easy to miss. It now
  raises `RendererNotInstalledError` with the exact fix
  (`playwright install chromium`) instead of a traceback.
- `--json-schema $schema: "https://json-schema.org/draft/2020-12/schema"`
  made `claude -p` reject the schema outright ("no schema with key or ref")
  before ever calling the API — the CLI's local validator only resolves
  the draft-07 meta-schema. Reverted `$schema` in `cheatsheet.schema.json`
  / `solve.schema.json` to `http://json-schema.org/draft-07/schema#`;
  `$defs`/`$ref` still resolve correctly under that declaration (`$ref` is
  a JSON Pointer lookup, independent of the declared draft).
- The `compress` stage could exit non-zero with an empty stderr after a
  real, multi-second API call, because `$ref`/`$defs`/`oneOf` in the
  `--json-schema`-supplied schema aren't reliably supported by `claude`'s
  structured-output handling. Added `schemas/generation/solve.gen-schema.json`
  and `schemas/generation/cheatsheet.gen-schema.json` — simplified,
  `$ref`/`oneOf`-free twins used only for `--json-schema`, while
  `src/claude/validator.py`'s post-generation `validate_schema()` still
  enforces the full, precise shape via the real schema files (Python's
  `jsonschema` library has no such restrictions). See ARCHITECTURE.md
  "Why two schema files per stage". Also made `src/claude/runner.py`
  surface stdout, not just stderr, on a non-zero exit, so a future silent
  failure isn't as opaque.
- With the schema crash above fixed, `compress` then failed differently:
  `"Reached maximum number of turns (4)"`, because the model tried to
  satisfy the prompt's hard code-line-count limit by literally attempting
  `Bash` (`wc -l` / a Python one-liner) to count, which `allowed_tools: ""`
  denies — twice, burning the entire turn budget on denied-tool retries.
  `prompts/claude/v1/solve.md`, `verify.md`, and `compress.md` now state
  up front that no tools are available and counts/verification must be
  done by inspection, and `claude.max_turns` in `config/settings.yaml`
  went from 4 to 8 as a buffer against the same pattern elsewhere. See
  ARCHITECTURE.md "Why the prompts explicitly say 'no tool access'".

### Changed
- Replaced the Pillow-based renderer with an HTML/CSS template
  (`src/rendering/templates/`) screenshotted to PNG by headless Chromium
  via Playwright (`src/rendering/render.py`), to match a richer visual
  reference (gradient headline, icon-circle section badges, colored
  diagram cells with pointer arrows, a two-panel valid/invalid comparison,
  a purple "why this works" reasoning panel) while staying fully
  deterministic and offline at render time (fonts and the contact card are
  embedded as base64 `data:` URIs). See ARCHITECTURE.md "Why HTML/CSS
  instead of Pillow".
- Replaced `cheatsheet.schema.json` / `solve.schema.json`'s single
  `visual_plan` field with an optional `diagrams` array (0-2 items, one of
  `array_pointers` or `comparison_states`) and an optional
  `reasoning_panel`, decided per-problem by Claude
  (`prompts/claude/v1/solve.md`'s "Diagram library" section) rather than
  forced into one fixed layout. See ARCHITECTURE.md "Diagram component
  library".

### Added
- Initial repository scaffold: architecture docs, versioned prompts, JSON
  schemas, pipeline module skeleton (`src/leetcode`, `src/claude`,
  `src/rendering`, `src/storage`, `src/state`), GitHub Actions workflows
  (`daily.yml`, `ci.yml`), Claude Code slash commands
  (`/solve-daily`, `/verify-daily`, `/test-pipeline`), and unit tests with
  mocked fixtures.
- Design decisions locked for v1: fetch LeetCode's Daily Challenge directly
  (no intermediate solutions repo), Claude Code performs all reasoning
  headless via `claude -p --bare`, rendering is 100% deterministic
  (Pillow, no AI image model), archival target is Google Drive only (no
  Telegram in v1 — see ARCHITECTURE.md).

- `assets/contact-card.png` wired in from the LeetCode visit card provided
  during setup — the render QA gate confirmed it fits cleanly bottom-right
  on the 1080x1350 canvas with no overlap or clipping.

### Notes
- This scaffold was generated from a design conversation (LeetCode ->
  cheat-sheet automation) and adapted to prioritize minimal recurring cost
  and reproducibility over the original hybrid AI-image-model design. See
  ARCHITECTURE.md for the full rationale on every deviation.
