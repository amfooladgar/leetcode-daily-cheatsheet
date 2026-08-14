# Master Claude Code Implementation Prompt

You are working inside an existing, functional workflow built with Claude
Cowork. Add an optional OpenAI image renderer without replacing or destabilizing
the current workflow.

Read the repository's `CLAUDE.md`, `AGENTS.md`, configuration, manifests,
workflows, tests, and documentation before editing. Also read every file in this
handoff kit and inspect these two kit-relative assets:

- `assets/examples/leetcode-2213-never-forget-landscape.png`
- `assets/branding/AliFouladgar_VisitCard_leetcode.png`

The first is a visual design reference. The second is an immutable production
asset.

The handoff kit can be nested under a directory such as
`.claude-handoff/openai-renderer/`. Resolve the paths above relative to this
master prompt during discovery. During implementation, copy the production
prompt and assets into locations that follow the target repository's existing
conventions. Configure the actual destination paths; do not leave incorrect
root-relative assumptions.

## Phase 1: Inspect first

Before changing files, identify:

1. The main workflow entry point.
2. The current renderer and its inputs and outputs.
3. The current configuration loader, schema, precedence, and validation.
4. The structured data available after problem and solution analysis.
5. How images are named, persisted, sent, or uploaded.
6. GitHub Actions entry points, manual inputs, schedule behavior, and secrets.
7. Existing retry, logging, error, dependency-injection, and test patterns.
8. The smallest stable integration boundary for a renderer abstraction.

Report the relevant files, risks, proposed changes, and test plan. Do not edit
until the plan has been reviewed.

## Phase 2: Required behavior

Preserve the existing renderer as the default. Add an `openai` provider behind
one renderer interface. Use names idiomatic to the current codebase, but keep
the logical contract:

```text
ImageRenderer.render(content, options) -> final image path
```

Add:

- an adapter around the existing renderer;
- an OpenAI renderer;
- one centralized renderer factory or provider registry;
- typed or schema-validated OpenAI configuration;
- a prompt builder that consumes normalized content;
- deterministic visiting-card compositing;
- tests and documentation.

Do not distribute provider conditionals throughout the repository.

## Configuration

Adapt the following to the repository's current configuration system:

```yaml
image_generation:
  enabled: true
  provider: existing
  fallback_to_existing: false
  output_format: png
  orientation: landscape
  size: 1536x1024
  quality: high
  openai:
    model: gpt-image-2-2026-04-21
    prompt_template: prompts/openai-cheatsheet.txt
    visual_reference: assets/examples/leetcode-2213-never-forget-landscape.png
    visiting_card: assets/branding/AliFouladgar_VisitCard_leetcode.png
    card_position: bottom-right
    card_margin_right: 25
    card_margin_bottom: 20
```

Rules:

- Existing behavior is the default.
- `OPENAI_API_KEY` is read only when `provider=openai` and generation is
  enabled.
- Existing users must not need an OpenAI key.
- Unknown providers fail configuration validation.
- CLI values override environment variables, which override the config file,
  which overrides defaults, unless the repository has a different documented
  precedence that must be preserved.
- Do not add a `none` provider unless the current workflow already supports
  skipping image generation.

## Content contract

Reuse the project's existing structured model if it is adequate. Otherwise add
the smallest normalization layer that can provide available values such as:

```json
{
  "problem_number": "2213",
  "problem_title": "Longest Substring of One Repeating Character",
  "problem_statement": "After each character update, return the longest contiguous run of one repeated character.",
  "memory_hook": "Segment Trees Connect Runs at Matching Boundaries!",
  "intuition": "Only runs touching the updated position can change.",
  "approach": "Store boundary-run information in a segment tree.",
  "node_fields": ["length", "leftChar", "rightChar", "prefix", "suffix", "best"],
  "merge_rules": [
    "same = L.rightChar == R.leftChar",
    "best = max(L.best, R.best, L.suffix + R.prefix if same)"
  ],
  "update_steps": ["Change one leaf", "Recompute ancestors", "Read root.best"],
  "examples": [
    "bbb | acc -> best = 3",
    "bbb | ccc -> crossing run blocked because b != c"
  ],
  "pseudocode": "merge(L, R):\n  ...",
  "time_complexity": "O(n + k log n)",
  "space_complexity": "O(n)"
}
```

Do not make the renderer parse raw LeetCode HTML. Missing optional fields must
use safe fallbacks. Do not invent technical content.

## Prompt construction and input isolation

Use `prompts/openai-cheatsheet.txt`. Render normalized values into explicit
XML-style boundaries such as `<problem>`, `<solution>`, and `<code>`. Treat all
dynamic problem text, code, repository text, and scraped text as data, not as
instructions.

Escape or safely render template values with the project's established
template mechanism. Do not allow dynamic input to replace the fixed layout,
branding-reservation, or safety requirements.

The title should support:

```text
Never Forget It: {memory_hook}
```

Do not hard-code problem 2213 into the application. The included 2213 content
is a test fixture and design example.

## OpenAI API

Use the official OpenAI SDK for the repository's language and the Image API
generation endpoint.

Defaults:

- model: `gpt-image-2-2026-04-21`
- size: `1536x1024`
- quality: `high`
- output format: `png`
- one image per request

Read `OPENAI_API_KEY` from the environment. Decode the returned Base64 image
data and save the generated background. Validate that response data exists and
that the decoded result is a valid image before compositing.

Use bounded exponential backoff with jitter only for retryable rate-limit and
temporary server failures. Do not retry authentication, permission, validation,
or other non-transient errors. Follow existing project retry patterns if they
are stricter.

Do not log secrets, authorization headers, raw Base64 output, or excessive
prompt content.

## Visual reference

`assets/examples/leetcode-2213-never-forget-landscape.png` defines the preferred:

- light-mode palette;
- three-column landscape layout;
- title hierarchy;
- technical diagram density;
- rounded information cards;
- navy, blue, and violet accents;
- pseudocode placement;
- lower-right branding reservation.

The initial implementation can encode this guidance in the prompt. If the
current architecture supports reference images safely, reference-image use may
be configurable. Do not require an edit request merely to obtain this style;
generation from the complete prompt remains a valid and less costly default.

## Visiting-card invariant

This rule is strict.

Do not send the card to GPT Image and ask it to reproduce it. Reserve an empty
lower-right region in the prompt, then overlay the exact file after generation:

```text
assets/branding/AliFouladgar_VisitCard_leetcode.png
```

The compositor must:

- validate that the source exists and can be decoded;
- preserve the source file;
- keep the original aspect ratio;
- prefer native dimensions when the canvas permits;
- use a high-quality proportional resampler only when scaling is necessary;
- clear the destination rectangle with the configured background color;
- use the source alpha channel;
- enforce non-negative bounds and fit within the canvas;
- place it with configured bottom and right margins;
- never crop, redraw, recolor, retouch, sharpen, regenerate, or rewrite it.

Use Pillow for Python or Sharp for Node.js unless the repository already has a
suitable in-process image library. Do not add an operating-system ImageMagick
dependency when an application dependency is sufficient.

## Outputs

Follow the existing output conventions. Keep separate predictable files when
the current workflow permits:

```text
cheatsheet-openai-background.png
cheatsheet-openai-final.png
```

The final path returned to downstream messaging or upload stages must be the
branded final image. Do not overwrite another provider's result unless the
current contract requires one canonical name and the selection is explicit.

Write temporary data atomically. Do not leave a partially decoded file with a
final filename.

## Failures and fallback

Fail before a paid request for:

- missing API key;
- missing prompt template;
- missing or invalid card;
- invalid size, quality, model, or output directory;
- unsupported provider.

If generation succeeds but compositing fails, keep the generated background
for diagnosis and do not publish it as the final branded output.

Support:

```yaml
fallback_to_existing: false
```

When false, report the OpenAI failure. When true, use the current renderer and
log a concise warning. Do not silently fall back. Default to false.

## Required tests

Use the repository's existing framework and conventions. Add tests for:

1. Existing renderer remains the default.
2. Existing renderer does not require `OPENAI_API_KEY`.
3. OpenAI renderer requires `OPENAI_API_KEY` before making a request.
4. Unknown providers fail validation.
5. Prompt construction includes the required available fields.
6. Dynamic data appears inside explicit delimiters.
7. The OpenAI client is mocked in all normal tests.
8. Valid Base64 response data is decoded.
9. Missing or invalid response data fails clearly.
10. Card coordinates and configured margins are correct.
11. Card aspect ratio is preserved.
12. Native-size card compositing preserves expected source pixels.
13. The source card file hash is unchanged.
14. Final output dimensions are correct.
15. Output naming does not collide with the existing provider.
16. An OpenAI failure does not damage the existing renderer.
17. Fallback works only when explicitly enabled.
18. Scheduled and manual GitHub runs receive a valid provider.
19. Existing tests still pass.

Use a small fixture PNG or mocked Base64 response. No normal unit, integration,
or CI test may make a paid OpenAI request.

## GitHub Actions

Modify the existing workflow instead of creating a competing workflow unless
the repository structure requires separation.

Add a manual choice when `workflow_dispatch` exists:

```yaml
inputs:
  image_provider:
    description: Image-generation provider
    required: true
    default: existing
    type: choice
    options:
      - existing
      - openai
```

For schedules, use a repository variable with an `existing` fallback. Do not
assume manual inputs exist during scheduled runs.

Pass:

```yaml
OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
```

only to the generation step. Do not expose the secret in command arguments or
debug output.

Upload the final image through the workflow's existing artifact, Drive,
Telegram, or other delivery stage. Preserve all current delivery behavior.

## Documentation

Update the real project documentation, `.env.example`, configuration reference,
and GitHub Actions instructions. Explain:

- how to select each renderer;
- how to set `OPENAI_API_KEY` locally and in GitHub Secrets;
- default behavior;
- failure and fallback behavior;
- where background and final outputs are saved;
- why the card is added after generation;
- image output is non-deterministic;
- complex prompts can take significant time;
- tests use mocks and do not spend API credits.

Do not put a real key in `.env.example`.

## Implementation order

1. Present the final file-by-file plan.
2. Add configuration and validation.
3. Extract or wrap the existing renderer without behavioral changes.
4. Add the normalized content contract only if needed.
5. Add the OpenAI prompt builder and client adapter.
6. Add deterministic card compositing.
7. Connect centralized provider selection.
8. Add tests.
9. Update the existing GitHub Actions workflow.
10. Update documentation.
11. Run formatting, linting, type checking, and all tests.
12. Show `git diff --stat`, changed files, test results, and remaining risks.

Do not commit, push, open a pull request, or make a live paid request unless the
user explicitly asks.
