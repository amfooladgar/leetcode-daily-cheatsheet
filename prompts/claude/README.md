# Prompts

Each file here is passed verbatim (with `{{placeholders}}` substituted by
`src/claude/runner.py`) as the prompt to `claude -p --bare`. Production
always reads from `prompts/claude/v1/` — the symlink-free convention is:
`src/claude/runner.py` takes a `prompt_version` argument (default `"v1"`,
set in `config/settings.yaml`) and reads
`prompts/claude/{prompt_version}/{stage}.md`.

## Pipeline order

1. `solve.md` — solve the problem from first principles. Input: the
   normalized problem JSON. Output: matches the "solve" shape consumed by
   `verify.md` (see schemas/cheatsheet.schema.json fields it populates).
2. `verify.md` — adversarial review of the solve output. Output: matches
   `schemas/verify.schema.json`.
3. `compress.md` — takes the verified content and fits it into the hard
   size limits from `config/settings.yaml` so the 1080x1350 canvas never
   overflows. Output: matches `schemas/cheatsheet.schema.json` exactly.
4. `title.md` — (folded into compress.md's `headline` field by default;
   kept standalone too in case you want to regenerate just the headline
   without re-running the whole compress step, e.g. `--regenerate-title`.)

## Versioning

If you materially change what a prompt asks for (not just wording), copy
`prompts/claude/v1/` to `prompts/claude/v2/` and bump
`claude.prompt_version` in `config/settings.yaml`. Every
`output/<date>/<problem>/content.json` records which `prompt_version`
produced it, so old cheat sheets stay reproducible even after you iterate.
