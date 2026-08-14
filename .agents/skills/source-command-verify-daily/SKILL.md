---
name: "source-command-verify-daily"
description: "Re-run just the Codex verification stage against the latest local solve output and manually sanity-check it"
---

# source-command-verify-daily

Use this skill when the user asks to run the migrated source command `verify-daily`.

## Command Template

Locate the most recent `output/<date>/<problem-number>/solve.json` (by
directory mtime) and its sibling `problem.json`. Then:

1. Read both files.
2. Read `prompts/Codex/v1/verify.md` so you know exactly what the
   automated adversarial pass checks.
3. Independently perform the same review yourself: trace the code in
   `solve.json.code` against every example in `problem.json.examples` by
   hand, check the boundary/edge cases verify.md lists, and check the
   claimed time/space complexity against what the code actually does.
4. Compare your manual findings to `verify.json` from the same run (if
   present) — do you agree with its `valid` verdict and `issues`? If you
   disagree, say exactly where and why; that's a signal
   `prompts/Codex/v1/verify.md` may need to be strengthened, not just that
   this one run was unlucky.

This is a development/debugging aid — it does not write any file or call
the actual pipeline. Use `/test-pipeline` to exercise the real automated
path end-to-end.
