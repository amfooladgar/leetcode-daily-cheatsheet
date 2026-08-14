# leetcode-daily-cheatsheet

Every morning, this pipeline reads that day's LeetCode Daily Challenge,
solves and verifies it with Claude Code, and produces a precise,
"never-forget-it" cheat sheet — sized for a LinkedIn portrait post — that
gets archived to Google Drive.

```
LeetCode Daily Challenge
        |
        v
  GitHub Actions (09:00 America/New_York)
        |
        v
  Claude Code  --- solves, adversarially verifies, tests, compresses
        |
        v
  cheatsheet.json  (schema-validated structured content)
        |
        v
  Deterministic HTML/CSS + Playwright renderer  --- 1080x1350 PNG, 4:5
        |
        v
  Google Drive: posts/LeetCode/<year>/<month>/
```

Claude owns correctness. The renderer owns text/code fidelity, and picks
per-problem diagrams (array-pointer / valid-vs-invalid comparison panels)
from a small deterministic component library — no AI image model is in the
loop by default (see [ARCHITECTURE.md](ARCHITECTURE.md) for why, and for
the diagram component library). An optional GPT-Image-based renderer also
exists (`image_generation.provider: "openai"` in `config/settings.yaml`,
or `--image-provider openai`) for anyone who wants GPT Image's full-card
generation instead — off by default; see ARCHITECTURE.md "Optional OpenAI
image renderer" and `docs/SETUP.md` step 3d.

## Quick start

```bash
git clone <your-fork-url> leetcode-daily-cheatsheet
cd leetcode-daily-cheatsheet
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium   # one-time: the renderer screenshots via headless Chromium
cp .env.example .env   # fill in ANTHROPIC_API_KEY etc. for local runs

# assets/contact-card.png is already in place (your LeetCode visit card) —
# swap it for a different image anytime by overwriting that file.

# Try the whole pipeline end-to-end without uploading anything
python -m src.main --dry-run
```

See [docs/SETUP.md](docs/SETUP.md) for the full one-time setup (Anthropic
key, Google Drive OAuth authorization, GitHub secrets, enabling the
schedule) and [docs/OPERATIONS.md](docs/OPERATIONS.md) for how to operate
it day to day (manual reruns, reading failures, rotating keys).

## Repository layout

```
.github/workflows/   Scheduled + CI GitHub Actions
.claude/commands/    Claude Code slash commands used during development
assets/              Static assets (your contact card)
config/settings.yaml Single source of runtime configuration (no secrets)
prompts/claude/      Versioned prompts sent to Claude Code, one per stage
schemas/             JSON Schemas that gate every stage's output
src/                 Pipeline implementation (see ARCHITECTURE.md)
tests/               Unit tests with mocked network calls
docs/                SETUP.md and OPERATIONS.md
```

## Status

This is a personal automation project. See [CHANGELOG.md](CHANGELOG.md) for
what shipped in each phase, and `state/manifest.json` (created at runtime)
for the run history.
