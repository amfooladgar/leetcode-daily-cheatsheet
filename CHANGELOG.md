# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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
