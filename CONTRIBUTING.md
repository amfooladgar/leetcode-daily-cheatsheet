# Contributing

Thanks for considering a contribution. This project is growing from a
personal LeetCode pipeline into a community-built, AI-powered
interview-prep tool — the daily DSA cheat sheet is the first surface, not
the only one. Contributions that extend it to other interview domains
(SQL, system design, ML/AI, behavioral) or other delivery channels are
very welcome.

## Before you start

- Skim [ARCHITECTURE.md](ARCHITECTURE.md) for the pipeline design and the
  reasoning behind key decisions (dual image renderers, schema validation,
  idempotency).
- `CLAUDE.md` / `AGENTS.md` (kept in sync) are the canonical rules for
  anyone — human or agent — changing this codebase: type hints, adapter
  boundaries per external service, no hard-coded credentials, schema
  validation on every Claude-generated object, and never publishing
  unverified code. Read them before opening a PR; reviews will hold you to
  them.
- Check open issues before starting significant work, especially ones
  tagged `good first issue` or `help wanted`, so effort doesn't collide.

## Development setup

```bash
git clone https://github.com/amfooladgar/leetcode-daily-cheatsheet.git
cd leetcode-daily-cheatsheet
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env   # fill in only what your change needs
```

Run the pipeline without touching Drive or Telegram:

```bash
python -m src.main --problem-slug two-sum --dry-run
```

## Making a change

- Keep `src/main.py` as thin orchestration; real logic belongs in the
  relevant stage module (`src/leetcode/`, `src/rendering/`, `src/storage/`,
  `src/state/`) so it stays independently testable.
- Every structured object crossing a stage boundary is a `pydantic` model
  (`src/leetcode/models.py`).
- Nothing outside `src/leetcode/` may know the LeetCode GraphQL schema;
  nothing outside `src/storage/` may know the Drive or Telegram API shapes.
- If you change a prompt in `prompts/claude/` or `prompts/openai/` in a way
  that materially changes its behavior, copy it to a new version rather
  than editing production behavior in place (see `prompts/claude/README.md`
  and `prompts/openai/README.md`).
- Never add a path that publishes to Drive, Telegram, or LinkedIn without
  going through the existing human-gated entry points described in
  `CLAUDE.md` ("LinkedIn posting").

## Tests

```bash
pytest -q
ruff check .
ruff format --check .
```

The suite mocks every network call (LeetCode GraphQL, Anthropic, OpenAI,
Google Drive, Telegram) and must pass offline with zero API keys set. Add
fixtures under `tests/fixtures/` rather than hitting real services. A
change is not done until `pytest -q` is green.

## Opening a PR

- Describe what changed and why, not just what.
- Note whether you exercised the change against a real problem
  (`python -m src.main --problem-slug <slug> --dry-run`) or tests only.
- Keep PRs scoped — one pipeline stage or feature per PR is easier to
  review than a broad sweep.

## Reporting bugs / proposing features

Open a GitHub issue. For bugs, include the command you ran, the relevant
`output/<date>/<problem-number>/*.json` artifacts if available, and
whether `--image-provider existing` reproduces it (helps isolate renderer
vs. pipeline issues). For feature proposals, a short description of the
interview domain, channel, or workflow you want to add is enough to start
a discussion — no need for a full design up front.
