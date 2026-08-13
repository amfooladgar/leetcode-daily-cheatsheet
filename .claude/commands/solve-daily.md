---
description: Run the full pipeline against today's LeetCode Daily Challenge in dry-run mode and summarize the result
---

Run the daily cheat-sheet pipeline against today's actual LeetCode Daily
Challenge, in dry-run mode (never touches Google Drive or the manifest):

```bash
python -m src.main --dry-run -v
```

Then:

1. Read `output/<today's date>/<problem-number>/problem.json`,
   `solve.json`, `verify.json`, and `content.json` and summarize what was
   fetched, what Claude decided, and whether verification passed on the
   first attempt or needed a regeneration.
2. Open `output/<today's date>/<problem-number>/cheatsheet.png` and
   describe what it looks like — is anything visually off (clipped text,
   an empty section, a section running into the contact card)?
3. If anything looks wrong, say specifically which stage/file is the
   likely cause (e.g. "compress.md let intuition run long — see
   prompts/claude/v1/compress.md") rather than just re-running blindly.

Do not modify Drive, `state/manifest.json`, or push anything — this command
is for local iteration only. See docs/OPERATIONS.md for the production
runbook.
