# Operations runbook

## Daily happy path

09:05 or 10:05 UTC (whichever is currently 09:05 America/New_York) ->
GitHub Actions runs `daily.yml` -> fetches today's Daily Challenge -> solves
-> verifies -> renders -> uploads to Drive -> commits the updated
`state/manifest.json` back to `main`. No action needed from you. Check your
Drive `posts/LeetCode/<year>/<month>/` folder whenever you want.

## Checking whether today's run succeeded

Repo -> Actions -> "Daily LeetCode Cheat Sheet" -> most recent run. Each
stage is a distinct step in the log (FETCHED, NORMALIZED, SOLVED, VERIFIED,
TESTED, COMPRESSED, RENDERED, QA_PASSED, UPLOADED — see ARCHITECTURE.md).
The failing step tells you exactly where it stopped; nothing after that
step ran.

## Re-running a specific day

```bash
# From your machine, or via Actions -> Run workflow -> set inputs
python -m src.main --date 2026-08-12 --force
```

`--force` bypasses the `state/manifest.json` idempotency guard for that one
date. Without `--force`, re-running a date that already succeeded is a
no-op by design (prevents duplicate Drive uploads from a retried Action).

## Testing against an old / specific problem (not today's daily)

```bash
python -m src.main --problem-slug two-sum --dry-run
```

`--dry-run` runs every stage through rendering but skips the Drive upload
and the manifest write, so you can iterate on prompts or the renderer
without touching Drive or state. Output lands in
`output/<date>/<problem-number>/` regardless.

## Selecting the image-generation provider for a run

The default `existing` renderer needs no extra flags. To try the optional
OpenAI renderer for one run (see ARCHITECTURE.md "Optional OpenAI image
renderer" and docs/SETUP.md step 3d for setup):

```bash
python -m src.main --problem-slug two-sum --dry-run --image-provider openai
```

Precedence: `--image-provider` CLI flag > `IMAGE_GENERATION_PROVIDER` env
var > `image_generation.provider` in `config/settings.yaml`. In GitHub
Actions, a manual `daily.yml` run lets you pick the provider from the
`image_provider` dropdown; the scheduled run always uses the
`IMAGE_GENERATION_PROVIDER` repository variable (Settings -> Secrets and
variables -> Actions -> Variables), falling back to `existing` if that
variable isn't set.

`existing` writes `output/<date>/<problem>/cheatsheet.png`. `openai`
writes two separate files in the same directory:
`cheatsheet-openai-background.png` (the raw GPT Image output, kept even on
a compositing failure, for diagnosis) and `cheatsheet-openai-final.png`
(the branded image actually uploaded/sent — this is what
`state/manifest.json`'s `image_filename` will name for that run).

## When the OpenAI renderer fails

Check the logged reason first — config problems (missing `OPENAI_API_KEY`,
missing/invalid `assets/contact-card.png`, a bad `image_generation.openai`
value) are caught before any paid request and are usually a one-line fix.
An actual API/decode/compositing failure records
`failure_stage: "render_openai"` in `state/manifest.json` and stops the run
(nothing is uploaded) unless `image_generation.fallback_to_existing: true`,
in which case the run logs a warning and continues with the `existing`
renderer instead. If `cheatsheet-openai-background.png` exists but
`cheatsheet-openai-final.png` doesn't, generation succeeded and compositing
failed — the background is kept specifically so you can inspect it.

## When Claude's verification fails

The run stops before rendering (see ARCHITECTURE.md "Failure policy") and
the log prints the `issues` array from `prompts/claude/verify.md`'s
response. The intermediate `solve` and `verify` JSON are kept in
`output/<date>/<problem-number>/` for inspection. Common causes: the
problem's official examples changed (rare), or the solve step picked an
approach that fails a boundary case verify.md caught — rerun with
`--force` after nothing needs fixing on your end; Claude re-attempts the
solve step fresh each run.

## When the renderer's QA gate fails

This means compressed content didn't fit the hard limits in
`config/settings.yaml` (`content.*_max_words`, `code_absolute_max_lines`) —
treat it as a bug in `prompts/claude/compress.md` or the limits themselves,
not a transient failure. Check `output/<date>/<problem-number>/content.json`
for what didn't fit, and `output/<date>/<problem-number>/cheatsheet.png` if
it got far enough to render before failing QA (the QA gate runs after
rendering, so a nearly-valid image is often already on disk).

## When the Drive upload fails

Manifest records `"drive": false` for that date but the render step
succeeded, so nothing is lost — the PNG is in the workflow run's uploaded
artifact (Actions -> the run -> Artifacts) even though it didn't reach
Drive. Common causes: `GOOGLE_DRIVE_FOLDER_ID` points at a folder your
Google account (the one you authorized in docs/SETUP.md step 3.6) doesn't
have access to, or the OAuth refresh token was revoked (Google Account ->
Security -> Third-party access) — re-run
`python scripts/authorize_google_drive.py` and update the
`GOOGLE_OAUTH_REFRESH_TOKEN` secret if so. Once fixed:

```bash
python -m src.main --date <that-date> --force
```

## Rotating credentials

- **Anthropic key**: create a new key in the Claude Console, update the
  `ANTHROPIC_API_KEY` GitHub secret, delete the old key in the Console.
  No code change needed.
- **Google Drive OAuth**: the refresh token doesn't expire on its own, so
  there's usually nothing to rotate. If you want to anyway (or it was
  revoked): re-run `python scripts/authorize_google_drive.py` and update
  the `GOOGLE_OAUTH_REFRESH_TOKEN` secret with the new value — the
  `GOOGLE_OAUTH_CLIENT_ID`/`GOOGLE_OAUTH_CLIENT_SECRET` pair only needs to
  change if you regenerate the OAuth client itself in Google Cloud
  Console.

## Changing the schedule time

Edit `schedule.target_hour` and `schedule.timezone` in
`config/settings.yaml` (what the Python check enforces) **and** the two
`cron:` lines in `.github/workflows/daily.yml` (what actually wakes the
runner up — cron is UTC and does not know about `config/settings.yaml`).
Keep both cron lines (the DST-straddling pair) unless you've picked a UTC
offset that never crosses a DST boundary for your timezone.

## Changing prompts

Edit files under `prompts/claude/`. Because production runs `claude -p
--bare` with the prompt file's contents passed explicitly, a prompt change
takes effect on the very next run with no other deployment step. If you
want reproducibility of *old* outputs, copy the prompt to
`prompts/claude/v2/` etc. before editing rather than overwriting — see
CLAUDE.md "Rules".

## Cost monitoring

`claude -p --output-format json` includes `total_cost_usd` per call — the
pipeline logs this at the end of each Claude stage. Anthropic Console and
Google Cloud billing are the sources of truth; Drive storage and API calls
at this volume (one small PNG + one small JSON per day) are free-tier.
