# Architecture

## Pipeline overview

```
FETCHED
   |  src/leetcode/client.py — GraphQL call to leetcode.com/graphql
   v
NORMALIZED
   |  src/leetcode/parser.py — strip HTML, produce Problem (pydantic model)
   v
SOLVED
   |  src/claude/runner.py + prompts/claude/solve.md
   |  Claude solves from first principles; NOT shown official/community
   |  solutions (see "Why solve from scratch" below)
   v
VERIFIED
   |  src/claude/runner.py + prompts/claude/verify.md
   |  Adversarial second pass: correctness, edge cases, complexity re-check.
   |  On failure: regenerate once, then verify again. Still invalid -> STOP.
   v
TESTED
   |  src/claude/validator.py — run the solution against every official
   |  example plus a few generated edge cases (empty input, single element,
   |  max constraint boundary). A failing test is a pipeline failure.
   v
COMPRESSED
   |  prompts/claude/compress.md — fit verified content into the hard word/
   |  line limits in config/settings.yaml so the canvas never overflows.
   v
RENDERED
   |  src/rendering/render.py — deterministic HTML/CSS layout (Jinja2
   |  template + embedded CSS design system), screenshotted to PNG by
   |  headless Chromium via Playwright: headline, problem statement,
   |  intuition + diagram(s), reasoning panel, approach steps, code block,
   |  complexity chips, contact card. No AI-generated pixels — see "Why
   |  HTML/CSS instead of Pillow" and "Why no AI image model" below.
   v
QA_PASSED
   |  src/rendering/render.py QA gate — exact 1080x1350, PNG, headline and
   |  code non-empty, no content overflowing the fixed canvas.
   v
UPLOADED
   |  src/storage/google_drive.py — posts/LeetCode/<year>/<month>/
   v
MANIFEST WRITTEN
      src/state/manifest.py — state/manifest.json, keyed by
      "<date>:<problem_number>" so a re-run is a no-op unless --force.
```

If a stage fails, its intermediate output (the last successfully produced
JSON/image) is kept in `output/<date>/<problem-number>/` for debugging —
nothing is silently discarded.

## Why solve from scratch instead of reading official solutions

LeetCode's own guidance is to attempt a problem independently before
consulting official/community solutions. Feeding Claude someone else's
solution and asking it to paraphrase would defeat the educational purpose
of a "never forget this" cheat sheet and risks reproducing copyrighted
write-ups. The pipeline only ever gives Claude the problem statement,
constraints, and examples — never a scraped solution.

## Why `--bare` in production

`claude -p --bare` skips auto-discovery of hooks, skills, plugins, MCP
servers, and CLAUDE.md, and forces API-key authentication instead of a
local login session. That means:

- A run behaves identically on any machine (a laptop, a GitHub Actions
  runner, a teammate's machine) because it can't accidentally pick up local
  configuration.
- Production prompts are fully specified by the files in `prompts/claude/`
  passed on the command line — nothing implicit.
- It starts faster and uses fewer tokens, since it isn't loading unrelated
  project context on every call.

CLAUDE.md still matters — it's what loads when you run interactive `claude`
sessions in this repo for development (see docs/OPERATIONS.md).

## Why the prompts explicitly say "no tool access"

`allowed_tools: ""` in `config/settings.yaml` means every `run_stage()`
call is pure reasoning — no Bash, no file access, no code execution. That
is deliberate: correctness is enforced by dedicated deterministic stages
(`src/claude/validator.py`'s `run_examples()` actually executes the
verified solution against every official example; the render QA gate
actually measures overflow), not by letting an LLM call out to check its
own work mid-stage.

In practice, `compress` hit "Reached maximum number of turns (4)" because
the model tried to satisfy `prompts/claude/v1/compress.md`'s hard
line-count limit precisely — twice attempting `Bash` to literally count
lines with `wc -l` / a Python one-liner, both denied since no tools are
allowed, burning through the turn budget on retries instead of just
reading the code and estimating. `prompts/claude/v1/solve.md`,
`verify.md`, and `compress.md` now all state up front that no tools are
available and that counting/verification must happen by inspection, not
execution — and `claude.max_turns` was raised from 4 to 8 as a buffer
against the same failure mode recurring anywhere else, since a few
recovered-from turns cost tokens but a hard stop at max-turns fails the
whole run.

The same root cause shows up in a second-order way: several schema fields
carry a hard `maxLength` (e.g. `reasoning_panel.bullets[i]` at 120 chars,
`headline` at 90) that Claude is expected to respect by estimation, exactly
like the compress line-count limit above. Estimation occasionally overshoots
by a handful of characters — a real solve run failed schema validation with
`reasoning_panel -> bullets -> 1: '...' is too long` at 124/120 chars. Since
there is no tool access to recount and self-correct, and discarding a whole
(paid) generation over a 4-character overshoot is wasteful, `src/claude/
validator.py`'s `clamp_to_schema()` runs between `run_stage()` and
`validate_schema()` on solve/verify/compress output: it walks the same
`$ref`/`$defs`/`oneOf` structure the real schema uses and truncates (with a
trailing `…`) any string that overshoots its `maxLength`, logging a warning
for each field it touches. Everything else — `enum`, `const`, `required`,
`minLength`, item-count bounds — still reaches `validate_schema()`
untouched, since those indicate a genuine shape/reasoning problem rather
than a length overshoot and should still fail the run.

The original design (see the ChatGPT conversation this repo grew out of)
proposed using an OpenAI image model to generate the schematic illustration
and only overlaying deterministic text/code on top. That stayed cut for v1,
even after the visual redesign described below, for three reasons that are
still why `existing` is the default and recommended provider today:

1. **Cost.** Every daily run would call a paid image API in addition to
   Claude. A fully deterministic renderer has zero marginal image-gen cost.
2. **Reliability.** Image models are not a reliable source of truth for
   exact code, numbers, or complexity notation — exactly the content a
   "cheat sheet" cannot get wrong.
3. **Reproducibility.** A deterministic renderer produces the same output
   for the same input every time, which matters for debugging and for
   `--force` re-runs.

The richer, diagram-heavy visual style (array cells with pointer arrows,
valid/invalid comparison panels, a "why this works" reasoning callout) is
achieved without an image model — see "Diagram component library" below.

An optional `openai` provider now exists behind
`image_generation.provider: "openai"` (see "Optional OpenAI image renderer"
below) for anyone who explicitly wants GPT Image's full-card generation
instead. It's a deliberate, documented exception to reasons 2 and 3 above —
selecting it means accepting non-deterministic, possibly-misrendered text
in exchange for a different visual style — never the default, and never
silently substituted for `existing`.

## Why HTML/CSS instead of Pillow

v1 rendered directly with Pillow: hand-rolled text wrapping, paragraph
fitting, and a line-based Python syntax highlighter, all drawing onto a
canvas with pixel-coordinate math. That was replaced with an HTML/CSS
template (`src/rendering/templates/cheatsheet.html.jinja2`) rendered by
headless Chromium via Playwright (`src/rendering/render.py`), once the
visual bar moved from "clean and legible" to matching a richer reference
design (gradient headline, icon-circle section badges, colored diagram
cells with pointer arrows, a two-panel valid/invalid comparison, a purple
reasoning panel, syntax-highlighted code). Reasons for the switch:

1. **Layout engine reuse.** Flexbox handles two-column rows, equal-height
   card matching, text wrapping, and overflow measurement for free — the
   Pillow renderer reimplemented all of that by hand (see the historical
   line-spacing/overflow bugs in git history before this pivot).
2. **Still fully deterministic and offline.** The page is one
   self-contained HTML string — fonts and the contact-card image are
   embedded as base64 `data:` URIs at render time (`_data_uri()` in
   `render.py`), so nothing is fetched over the network and output is
   reproducible byte-for-byte given the same input, same as the Pillow
   renderer's guarantee.
3. **Same QA contract.** `render_cheatsheet()` still returns the
   `QAResult` dataclass (`passed`, `width`, `height`, `format`, `checks`,
   `warnings`, `failed_checks`) `src/main.py` already expects — overflow is
   detected via `scrollHeight` against the fixed 1080x1350 viewport instead
   of a Pillow line-fit loop, but it's the same "stop, never silently
   resize or clip" policy.

## Diagram component library

`schemas/cheatsheet.schema.json` and `schemas/solve.schema.json` define an
optional `diagrams` array (0-2 items) and an optional `reasoning_panel`.
Claude (`prompts/claude/v1/solve.md`, "Diagram library" section) decides
per-problem whether a diagram genuinely clarifies the solution — most
array/string/two-pointer/sliding-window problems do; most pure-math or
bit-manipulation problems don't — and never invents one that doesn't fit.

Two components, each with a corresponding Jinja2 partial in
`src/rendering/templates/`:

- **`array_pointers`** (`_array_pointers.html.jinja2`) — one row of cells
  with optional pointer labels, for two pointers / sliding window / prefix
  sums / string scanning.
- **`comparison_states`** (`_comparison_states.html.jinja2`) — 2-3
  side-by-side cases (e.g. valid vs. invalid), each with its own optional
  mini array. This is the component the "Non-Shrinking Sliding Window"
  reference card's design was built around.

Both share a `cell_row` Jinja2 macro (`_macros.html.jinja2`) so cell/pointer
markup stays identical wherever it's used. `reasoning_panel`
(`_reasoning_panel.html.jinja2`) is a short "why this works" callout,
independent of which diagram (if any) is present.

When a cheat sheet has no `diagrams` and no `reasoning_panel` — a
legitimate, expected case for problems where neither adds clarity — the
template falls back to a single-column layout with a compact `Example:`
line instead of leaving empty space or forcing a diagram to fit
(`tests/fixtures/sample_cheatsheet_no_diagram.json` exercises this path).

## Why two schema files per stage

Each of `solve` and `compress` has two schema files: `schemas/solve.schema.json`
/ `schemas/cheatsheet.schema.json` (the real, fully-expressive contract —
`$ref`/`$defs` for the shared `cell`/`pointerLabel` shapes, `oneOf` +
`const` to discriminate `array_pointers` vs. `comparison_states`, exact
`minLength`/`maxLength`/`minItems`/`maxItems` bounds) and a second,
deliberately simpler twin under `schemas/generation/` (`solve.gen-schema.json`
/ `cheatsheet.gen-schema.json`).

The split exists because these two files are consumed by two different
things with different levels of JSON Schema support:

- **`src/claude/runner.py`'s `--json-schema` flag** feeds the schema into
  `claude`'s structured-output/tool-call mechanism, which only supports a
  subset of JSON Schema (`type`, `properties`, `required`,
  `additionalProperties`, `enum`, `const`, `items`, and `type` arrays for
  nullable fields — the last two count toward a union-type complexity
  budget). `oneOf`/`anyOf`/`allOf` are explicitly unsupported at the top
  level of a tool's `input_schema` and are not documented as supported
  anywhere else either; `$ref`/`$defs` aren't addressed in Anthropic's
  structured-outputs docs at all. In practice, giving `--json-schema` the
  full `oneOf` + `$ref`-heavy `cheatsheet.schema.json` made the `compress`
  stage exit non-zero with an empty stderr after a real, ~60s API round
  trip — the schema was accepted at parse time (unlike the earlier
  `$schema: .../2020-12` mismatch, which failed instantly) but something in
  validating the model's actual structured response against it crashed
  silently. The official Anthropic SDKs work around exactly this by
  stripping unsupported keywords from the wire schema and validating the
  response against the original schema client-side afterward — the
  `schemas/generation/` split does that same thing explicitly, since the
  `claude` CLI doesn't do it for a caller-supplied `--json-schema`.
- **`src/claude/validator.py`'s `validate_schema()`** runs *after*
  generation, using the Python `jsonschema` library, which has no such
  restrictions — it fully supports `$ref`/`$defs`/`oneOf`/`const`/length
  and count bounds. This is what actually enforces the precise shape
  (`main.py` always validates against the real `schemas/*.schema.json`,
  never the generation twin).

Practically: `schemas/generation/*.gen-schema.json` replaces `$ref`-shared
definitions with duplicated inline objects, replaces the `oneOf`
discriminated union with one permissive object schema plus an `enum`
discriminator (`component`) and description text explaining which fields
belong to which shape, and replaces every `minLength`/`maxLength`/
`minItems`/`maxItems` with the same bound stated in a `description` instead
— the model treats it as guidance either way, and the real backstops are
`prompts/claude/v1/compress.md`'s hard limits and the render QA gate's
overflow check, not schema-level string-length enforcement. `reasoning_panel`
in the generation schemas is a plain `"type": "object"` (no `null`) for the
same reason nullable `type` arrays are worth avoiding where they're not
needed — the prompts instruct Claude to omit the key entirely rather than
set it to `null` when a diagram or reasoning panel doesn't apply, which the
`(...).get("diagrams") or []` / `.get("reasoning_panel")` call sites in
`src/rendering/render.py` and `src/main.py` already handle identically to
an explicit `null` — see `prompts/claude/v1/solve.md` and `compress.md`.

If you add a field to `cheatsheet.schema.json` or `solve.schema.json`, add
the matching (simplified) field to its `schemas/generation/` twin too, or
`--json-schema` will silently reject/strip it via `additionalProperties:
false` before the model ever gets a chance to populate it.

## Why GitHub Actions, not ChatGPT Scheduled Tasks or n8n

- GitHub Actions gives version-controlled workflow definitions, encrypted
  secrets, per-run logs, manual re-dispatch (`workflow_dispatch`), and free
  minutes for a low-frequency personal job — all in the same repo as the
  code.
- ChatGPT Scheduled Tasks cannot access files inside a ChatGPT Project, so
  prompts/config would have to live somewhere else anyway, and the surface
  is a product UI rather than a reviewable diff.
- n8n (or similar) adds a whole extra hosted service for what is, at its
  core, "run a Python script once a day."

## Why fetch LeetCode directly instead of from a personal solutions repo

The original two-repo design (a DSA solutions repo + a separate automation
repo) added a git-push-triggers-processing step that isn't needed once the
Daily Challenge itself is the trigger. Fetching directly from LeetCode's
GraphQL endpoint removes an entire repository, an entire "did my push
already get processed" state machine, and a coupling to how you personally
organize your solutions repo. The trade-off, documented here on purpose: **LeetCode
does not publish this GraphQL interface as a supported public API.** It is
the same endpoint leetcode.com's own web client uses, is widely relied on by
community tooling, and can change without notice. `src/leetcode/client.py`
isolates every LeetCode-specific assumption behind a small adapter so a
breaking change is a one-file fix, and the pipeline fails loudly (not
silently) if the response shape changes.

## Why OAuth instead of a service account

`src/storage/google_drive.py` originally authenticated as a Google Cloud
service account, on the reasoning that GitHub Actions has no browser to
complete an interactive OAuth consent screen. That reasoning was correct
but incomplete — it missed that a service account has **no Drive storage
quota of its own**. It can create empty folders (metadata only, no bytes),
but the moment it tries to upload actual file content — even into a folder
a real person shared with it as Editor — Google rejects it:

```
403 storageQuotaExceeded: "Service Accounts do not have storage quota.
Leverage shared drives, or use OAuth delegation instead."
```

Shared Drives (the API error's first suggestion) are a Google Workspace
feature and aren't available on a personal/consumer Google account — the
one this pipeline is designed around (see `.env.example`'s
`GOOGLE_DRIVE_FOLDER_ID`, which points into someone's own "My Drive", not
an org's Shared Drive). Domain-wide delegation (a workaround for the same
underlying issue) is also Workspace-admin-only. That leaves OAuth
delegation — authenticating as the actual human Drive account, the same
one that owns the target folder — which works for any Google account and
needs no paid Workspace plan, matching the "minimal cost" constraint this
whole project was built under.

The trade-off: OAuth needs one interactive consent step, which GitHub
Actions genuinely can't do. `scripts/authorize_google_drive.py` runs that
step locally, once, in your own browser (`google_auth_oauthlib`'s
`InstalledAppFlow.run_local_server()`), and prints a refresh token.
Refresh tokens for this OAuth flow don't expire on their own — only if
explicitly revoked (Google Account -> Security -> Third-party access) or
unused for 6+ months — so this is a true one-time step, not a recurring
one, and `src/storage/google_drive.py`'s `_load_credentials()` uses it
completely non-interactively from then on (`google-api-python-client`
refreshes the short-lived access token automatically). See docs/SETUP.md
step 3.

## Daylight saving time

GitHub Actions cron is UTC-only and America/New_York alternates between
UTC-4 (EDT) and UTC-5 (EST). `daily.yml` schedules the job at **both**
13:05 and 14:05 UTC every day; `src/main.py` checks the actual wall-clock
hour in `America/New_York` via `zoneinfo` and no-ops if it isn't the
configured `target_hour` (09:00 by default). Combined with the
idempotency manifest, this guarantees exactly one publish per day
regardless of DST.

## Failure policy

| Stage failure          | Action                                              |
|-------------------------|------------------------------------------------------|
| LeetCode fetch           | Retry (`tenacity`, 3 attempts) -> stop, no artifact  |
| Claude solve              | Retry once -> stop                                   |
| Claude verify (invalid)   | Regenerate solution once, re-verify -> stop if still invalid |
| Example/edge-case tests   | Never publish; stop                                  |
| Renderer / QA gate        | Stop (this is a bug, not a transient failure)        |
| Google Drive upload       | Retry -> stop; manifest marks `drive: false`         |
| Telegram send              | No retry -> continue; manifest marks `telegram: false` |

Drive and Telegram are independent, non-blocking delivery stages: either
one failing marks its own manifest flag `false` and the run still exits
non-zero (so CI surfaces it), but never discards the rendered artifact and
never prevents the other stage from running. There is no image-generation
stage in v1's default path, so that row from the original design was
removed rather than left as dead policy — the "Renderer / QA gate" row
above covers the `existing` provider's overflow-recovery-then-stop
behavior. The optional `openai` provider (see "Optional OpenAI image
renderer" below) is the one exception: its failure stops the run and
records `failure_stage: "render_openai"` in the manifest only when
`image_generation.fallback_to_existing` is `false` (the default); when
`true`, the factory falls back to the `existing` provider instead of
stopping.

## Optional OpenAI image renderer

`image_generation.provider: "openai"` in `config/settings.yaml` (default:
`"existing"`) switches image generation to GPT Image generating the
*complete* visual cheat sheet, instead of the deterministic HTML/CSS
renderer. This is a deliberate, explicit exception to "Why no AI image
model" above — unlike the smaller-scope idea `prompts/future/openai-diagram.md`
originally sketched (an AI-generated illustration composited *underneath*
deterministic text, kept as an unwired reference), this provider lets GPT
Image render exact code, pseudocode, and complexity as pixels. It exists
because it was explicitly requested as a full alternative renderer, not
because the reliability concerns above stopped applying — they didn't.
Selecting it is an informed trade-off, never the default.

**Provider factory (`src/rendering/factory.py`).** The only place that
branches on `image_generation.provider`. `src/main.py` calls
`render_cheatsheet_with_provider()` and nothing else — no provider
conditionals exist anywhere else in the pipeline, the delivery adapters, or
the tests that don't specifically target this feature. Resolution order:
`--image-provider` CLI flag > `IMAGE_GENERATION_PROVIDER` env var >
`config/settings.yaml`'s `image_generation.provider`. `src/main.py`
validates the resolved provider's configuration immediately after loading
settings — before FETCHED, so a broken `openai` config (missing key,
template, or card; invalid size/quality/model) fails before any Anthropic
tokens are spent, not just before the OpenAI request itself.

**`src/rendering/existing_provider.py`** wraps the unmodified
`src/rendering/render.py` renderer plus its overflow-recovery retry (see
"canvas-overflow" in CHANGELOG.md), returning the shared
`src/rendering/base.py::RenderResult` shape.

**`src/rendering/openai_provider.py`** is the actual `openai` provider:

1. Validates config (size/quality/model, `OPENAI_API_KEY`, the prompt
   template file, and the card) *before* any paid request.
2. Builds the prompt (`src/rendering/openai_prompt.py`) from the same
   `schemas/cheatsheet.schema.json` shape the existing renderer consumes —
   no separate normalization layer. Every dynamic field is XML-escaped
   into explicit `<problem_statement>`/`<example>`/`<code>`/etc. tags in
   `prompts/openai/v1/cheatsheet.txt`, and the prompt instructs the model
   to treat that content as untrusted data, not instructions — the
   prompt-injection boundary the content contract requires.
3. Calls the OpenAI Image API (`gpt-image-2-2026-04-21`, `1536x1024`,
   `quality: high` by default), decodes and validates the returned
   base64 PNG, and writes it atomically to
   `output/<date>/<problem>/cheatsheet-openai-background.png`.
4. Retries only `RateLimitError` / `APIConnectionError` /
   `InternalServerError` with the existing `src/utils/retry.py` capped
   backoff helper (reused as-is, no jitter — see that module's docstring).
   Auth/permission/validation errors (`AuthenticationError`,
   `PermissionDeniedError`, `BadRequestError`, `NotFoundError`) never
   retry.
5. Hands off to `src/rendering/card_compositor.py` to overlay the *exact*
   `assets/contact-card.png` — the model is never asked to draw the card.
   Two live smoke tests showed GPT Image does not reliably honor a
   requested reservation size, even a padded one worded as a minimum (see
   CHANGELOG.md for both runs), so the *prompt* request and the *actual
   placement* are handled by two different, independent mechanisms rather
   than one trusted number:
   - **Prompt-side (a nudge, not a guarantee).** The reserved region
     requested in the prompt is sized via
     `card_compositor.compute_reserved_region()`, which pads the card's
     canvas-fit box (`compute_card_box()`) by
     `image_generation.openai.card_reservation_safety_margin` (default
     0.2 / 20%), and `prompts/openai/v2/cheatsheet.txt` states it as a
     minimum ("err on the side of larger"). This alone was insufficient in
     both live tests.
   - **Compositor-side (the actual guarantee).** Before placing the card,
     `card_compositor.detect_blank_region()` scans the *generated*
     background itself near the configured corner and returns the real
     blank rectangle found there (capped at the padded reservation size).
     The card is then fit to *that* measured space (`composite_card()`'s
     `available_width`/`available_height` params), not the requested size.
     If the detected space would force the card below
     `image_generation.openai.card_min_detected_scale` (default 0.4) of
     its native size, compositing fails loudly instead of publishing an
     illegible or overlapping card. Three live smoke tests (see
     CHANGELOG.md) drove the actual algorithm:
     - Height and width are each measured across several probe
       lines, taking the *minimum* extent found, so a genuine content
       intrusion on any one line can only shrink the result, never inflate
       it.
     - A short (`max_gap`, default 4px) non-blank interruption on a probe
       line is tolerated and skipped over if real blank space resumes
       right after it — a card can safely sit over a thin panel-border
       line; only a sustained non-blank run counts as real content.
     - Width is probed only within `min(max_height, detected_height)`, not
       the full requested window — a panel positioned beyond the height
       the card will actually occupy must not zero out width for no real
       reason.
     - The reference "blank" color is the configured `card_clear_hex`,
       deliberately not sampled from the corner pixel itself — if content
       extends all the way into the corner, that pixel is content, not
       background, and self-sampling would silently treat that content's
       color as blank.
     `image_generation.openai.card_margin_right`/`card_margin_bottom`
     (default 45/35, widened from an initial 25/20) also give rounded
     panel corners more room to clear the card's footprint in the first
     place.

   Either way, the compositor still clears the destination rect, preserves
   the card's alpha channel and aspect ratio (proportional downscale only,
   never crop or recolor), clamps placement to the canvas, and writes the
   branded result atomically to `cheatsheet-openai-final.png` — a
   separate, predictable name from the existing renderer's
   `cheatsheet.png`, so neither provider can overwrite the other's output.
   If compositing fails, the background is kept for diagnosis and the
   final file is never written.

**Fallback (`image_generation.fallback_to_existing`, default `false`).**
Only the factory decides this. When `false` (default), an `openai` failure
propagates and the run fails loudly with the reason recorded in
`state/manifest.json`. When `true`, the factory logs a warning and falls
back to the existing renderer — an `openai` failure never damages or
blocks the existing renderer's own path either way.

**Tests** (`tests/test_openai_renderer.py`, `tests/test_card_compositor.py`,
`tests/test_image_provider_factory.py`) mock the OpenAI client entirely —
no test spends real API credits, matching CLAUDE.md's testing rule.
