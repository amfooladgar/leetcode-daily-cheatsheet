# Prompt 1: Read-only discovery

You are in an existing, working workflow built with Claude Cowork. Do not edit
files yet.

Read all applicable `CLAUDE.md`, `AGENTS.md`, README, manifest, lockfile,
configuration, workflow, and test files. Trace the current pipeline end to end.

Inspect this handoff kit and these specific files:

- `MASTER_IMPLEMENTATION_PROMPT.md`
- `prompts/openai-cheatsheet.txt`
- `examples/config.example.yml`
- `examples/cheatsheet-content.example.json`
- `assets/examples/leetcode-2213-never-forget-landscape.png`
- `assets/branding/AliFouladgar_VisitCard_leetcode.png`

Identify:

1. The main workflow entry point.
2. The current image renderer and its call sites.
3. Configuration loading, precedence, and validation.
4. Available normalized problem and solution fields.
5. Output paths and downstream delivery consumers.
6. GitHub Actions manual and scheduled entry points.
7. Existing error, logging, retry, test, and dependency patterns.
8. The smallest clean integration boundary for an optional renderer.

Return:

- a concise architecture map;
- relevant files with their responsibilities;
- proposed files to add and modify;
- the configuration design adapted to this repository;
- backward-compatibility risks;
- a test plan;
- unanswered questions that materially affect the implementation.

Do not implement until the plan is approved.

