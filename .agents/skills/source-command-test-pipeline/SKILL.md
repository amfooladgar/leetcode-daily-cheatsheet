---
name: "source-command-test-pipeline"
description: "Run the automated test suite and a safe end-to-end dry run against a specific problem, then report results"
---

# source-command-test-pipeline

Use this skill when the user asks to run the migrated source command `test-pipeline`.

## Command Template

1. Run the automated test suite and report pass/fail per file, not just
   the total:
   ```bash
   python -m pytest -q
   ```
2. Run a full pipeline dry run against a stable, well-known problem so the
   run doesn't depend on today's Daily Challenge (never uploads or writes
   the manifest):
   ```bash
   python -m src.main --problem-slug two-sum --dry-run -v
   ```
3. Report, in order: whether the test suite passed, whether the dry run
   completed all stages (FETCHED through QA_PASSED — see ARCHITECTURE.md),
   any warnings logged (e.g. missing contact card, rendering overflow),
   and the path to the resulting
   `output/<date>/1/cheatsheet.png` so it can be reviewed visually.
4. If either step fails, stop and report the exact failing stage/test
   rather than attempting fixes automatically — this command is a
   diagnostic, not an auto-repair.
