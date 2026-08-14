# Project

Automated daily pipeline that reads the LeetCode Daily Challenge, solves and
verifies it with Claude Code, and renders a "Never Forget" cheat-sheet
(1080x1350 PNG, LinkedIn portrait 4:5) that is archived to Google Drive.

This file is read automatically by interactive `claude` sessions in this
repo. The production pipeline invokes `claude -p --bare`, which does **not**
read this file by design (see ARCHITECTURE.md, "Why --bare") — production
prompts live entirely in `prompts/claude/*.md` so a run is reproducible
independent of anyone's local Claude Code memory.

# Architecture (short version — see ARCHITECTURE.md for the full diagram)

LeetCode Daily Challenge
  -> normalize problem (src/leetcode/)
  -> Claude solves from first principles (prompts/claude/solve.md)
  -> Claude adversarial verification (prompts/claude/verify.md)
  -> executable tests against official examples
  -> Claude compresses content to fit the canvas (prompts/claude/compress.md)
  -> deterministic HTML/CSS + Playwright renderer (src/rendering/) -> 1080x1350 PNG
  -> Google Drive (src/storage/google_drive.py)
  -> manifest write (src/state/manifest.py)

The deterministic HTML/CSS renderer (`image_generation.provider: "existing"`
in `config/settings.yaml`) is the production-safe default, kept at
near-zero marginal cost with no source of rendered-text errors — see
ARCHITECTURE.md "Why no AI image model" for the full rationale. An optional
`openai` provider (`src/rendering/openai_provider.py`) also exists, off by
default, for anyone who explicitly opts in via
`image_generation.provider: "openai"` — see ARCHITECTURE.md "Optional
OpenAI image renderer" before touching it.

# Rules

- Python 3.12. Use type hints everywhere; no bare `except Exception` without
  re-raising with added context.
- Use `pydantic` models (src/leetcode/models.py) for every structured object
  that crosses a stage boundary (problem, cheatsheet content, manifest entry).
- Every external service (LeetCode, Anthropic, Google Drive) lives behind a
  thin adapter module. Nothing outside `src/leetcode/` may know the LeetCode
  GraphQL schema. Nothing outside `src/storage/` may know the Drive API shape.
- Never hard-code credentials. Read them only from environment variables
  (see .env.example for the full list). Fail fast with a clear error message
  if a required secret is missing — do not silently skip a stage.
  `OPENAI_API_KEY` is the one exception to "always required": it is read
  only when `image_generation.provider` resolves to `"openai"`, and the
  default `existing` provider must never require it.
- All Claude-generated content (problem understanding, code, complexity
  claims) MUST pass `jsonschema` validation against `schemas/*.json` before
  it is allowed to reach the renderer. A schema failure is a pipeline
  failure, not a warning.
- A failed adversarial verification (`prompts/claude/verify.md` returns
  `"valid": false` after one regeneration attempt) MUST stop the run before
  rendering or uploading anything. Never publish unverified code.
- Every run must be idempotent: re-running the same date/problem must not
  create a duplicate Drive upload. Idempotency is enforced via
  `state/manifest.json`, keyed by problem number + date + content hash.
- Image output dimensions are provider-specific, each enforced with an
  assertion in that provider's own QA gate, not just a config default:
  - `existing` (the default, production-safe renderer): final image is
    exactly 1080x1350 PNG. This is the recommended provider whenever exact
    text, code, formulas, or byte-for-byte repeatability matter.
  - `openai` (explicit, non-default opt-in — `image_generation.provider:
    "openai"`): final image matches `image_generation.openai.{width,height}`
    (1536x1024 by default), NOT the `existing` provider's 1080x1350 —
    never apply that assertion to openai output. GPT Image output is
    non-deterministic and can misrender text/code/formulas/layout;
    selecting this provider means knowingly accepting that. The original
    `assets/contact-card.png` is always composited onto the generated
    background after generation — never sent to the model to redraw.
- Use at most three accent colors (see config/settings.yaml `design.accent_hex`).
- The contact card (`assets/contact-card.png`) is an immutable source asset —
  never regenerate or resize the source file itself, only scale it on
  composite.
- Prompts are versioned. If you materially change a prompt's behavior, copy
  it to a new version rather than silently editing production behavior
  (see prompts/claude/README.md).

# Common commands

```bash
# Install deps
pip install -r requirements.txt

# Run the full pipeline without uploading anywhere (safe to run anytime)
python -m src.main --dry-run

# Test against a specific problem instead of waiting for tomorrow's daily
python -m src.main --problem-slug two-sum --dry-run

# Re-run a specific date, bypassing the idempotency guard
python -m src.main --date 2026-08-12 --force

# Run only up through rendering, skip Drive entirely
python -m src.main --skip-drive

# Try the optional OpenAI renderer instead of the default (requires
# OPENAI_API_KEY -- see ARCHITECTURE.md "Optional OpenAI image renderer")
python -m src.main --problem-slug two-sum --dry-run --image-provider openai

# Run the test suite (must be green before any change is considered done)
pytest -q

# Lint
ruff check .
```

# Testing

Run `pytest -q` before completing any change. Tests use fixtures in
`tests/fixtures/` and mock all network calls (LeetCode GraphQL, Anthropic,
Google Drive) — the suite must pass with zero network access and zero API
keys set.

# Code quality

Prefer small pure functions over large orchestration blocks. Keep
`src/main.py` as thin wiring between stages — real logic belongs in the
stage's own module so it's independently testable.
