# Claude Code Handoff: Optional OpenAI Cheat-Sheet Renderer

This kit is designed for an existing, working Claude Cowork repository. It does
not assume that the project uses Python, Node.js, YAML, or a specific folder
layout. Claude Code must inspect the repository first and adapt the feature to
the project's existing architecture.

## Desired result

Keep the current renderer as the default and add an optional OpenAI renderer:

```text
Existing workflow
      |
      v
Normalized cheat-sheet content
      |
      +-- provider = existing --> current renderer --> current output
      |
      +-- provider = openai  --> GPT Image 2 --> exact card overlay --> final PNG
```

The OpenAI renderer must:

- be disabled unless the user selects it;
- use the OpenAI Image API and `gpt-image-2-2026-04-21` by default;
- use `1536x1024` landscape output by default;
- keep the current workflow and renderer behavior unchanged;
- insert the supplied visiting card after image generation;
- never ask the image model to redraw the visiting card;
- support mocked tests without a paid API call;
- expose explicit failure and fallback behavior.

## Included files

```text
README.md
MASTER_IMPLEMENTATION_PROMPT.md
prompts/
  01-discovery.md
  02-implementation.md
  03-verification.md
  04-live-smoke-test.md
  05-final-review.md
  openai-cheatsheet.txt
examples/
  config.example.yml
  cheatsheet-content.example.json
  github-actions-fragment.yml
  env.openai.example
assets/
  SHA256SUMS
  examples/leetcode-2213-never-forget-landscape.png
  branding/AliFouladgar_VisitCard_leetcode.png
```

## Before Claude Code

1. Copy this complete folder into the existing project. A temporary location
   such as `.claude-handoff/openai-renderer/` is fine. The files in this kit are
   source material for Claude Code. During implementation, Claude Code must copy
   the production prompt and image assets to paths that match the repository's
   conventions, then configure those actual paths.
2. Check the repository state:

   ```bash
   git status
   ```

3. If the repository is clean, create a feature branch:

   ```bash
   git switch -c feature/openai-image-renderer
   ```

4. If it is not a Git repository, create a baseline before implementation:

   ```bash
   git init
   git add .
   git commit -m "Snapshot working Claude Cowork workflow"
   git switch -c feature/openai-image-renderer
   ```

5. Start Claude Code from the existing project root:

   ```bash
   claude
   ```

6. If the project does not have a useful `CLAUDE.md`, run `/init` and review the
   proposed project instructions before continuing.

7. In Claude Code, give the exact kit path. For example:

   ```text
   Read @.claude-handoff/openai-renderer/MASTER_IMPLEMENTATION_PROMPT.md and
   every file it directly references. Start with the required read-only
   discovery phase. Do not edit until you show me the repository-specific plan.
   ```

## Recommended Claude Code sequence

Use Plan mode for the first prompt.

1. Paste `prompts/01-discovery.md`.
2. Review the architecture report and correct any wrong assumptions.
3. Paste `prompts/02-implementation.md`, or paste
   `MASTER_IMPLEMENTATION_PROMPT.md` for the complete request in one message.
4. Let Claude Code implement the approved plan.
5. Paste `prompts/03-verification.md`.
6. Only after mocked verification succeeds, set `OPENAI_API_KEY` locally and
   paste `prompts/04-live-smoke-test.md`.
7. Inspect the image and the diff.
8. Paste `prompts/05-final-review.md`.
9. Commit or push only after the final review passes.

You can reference this complete folder in Claude Code with `@` and select its
files. Reference the two PNG files explicitly when discussing their roles. If
the kit is under a prefix such as `.claude-handoff/openai-renderer/`, resolve
all kit-relative paths under that prefix during discovery.

## Configuration goal

Adapt this shape to the existing configuration conventions:

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

The existing provider must remain the default. Users of the current workflow
must not need `OPENAI_API_KEY`.

## Local selection examples

Use the project's existing CLI or configuration format. Desired behavior:

```bash
IMAGE_GENERATION_PROVIDER=existing <current-run-command>
```

```bash
OPENAI_API_KEY="configured-locally" \
IMAGE_GENERATION_PROVIDER=openai \
<current-run-command>
```

Never store the key in the repository, prompt files, logs, artifacts, or a
committed `.env` file.

## Strict branding rule

The model-generated image is not authoritative for branding. The file
`assets/branding/AliFouladgar_VisitCard_leetcode.png` is the authoritative card.
The application must composite it after generation with Pillow, Sharp, or an
equivalent library already used by the project.

The implementation must preserve:

- all source pixels when inserted at native size;
- the original aspect ratio when proportional scaling is required;
- the alpha channel;
- the source file hash;
- the name, portrait, URLs, usernames, colors, border, and glow.

The original asset hashes are stored in `assets/SHA256SUMS`. Use them to verify
that the assets were copied correctly and that the source card remains
unchanged during tests.

## API behavior to implement

Use the official OpenAI SDK and the Image API generation endpoint. GPT Image
responses contain Base64 image data. Decode it and save it as PNG.

Conceptual Python form:

```python
result = client.images.generate(
    model=config.model,
    prompt=rendered_prompt,
    size=config.size,
    quality=config.quality,
    output_format="png",
)

image_bytes = base64.b64decode(result.data[0].b64_json)
```

Conceptual Node.js form:

```javascript
const result = await openai.images.generate({
  model: config.model,
  prompt: renderedPrompt,
  size: config.size,
  quality: config.quality,
  output_format: "png",
});

const imageBytes = Buffer.from(result.data[0].b64_json, "base64");
```

Claude Code must confirm the installed SDK version and exact parameter names
when implementing. It must use the project's existing dependency manager and
lockfile.

## Acceptance criteria

- Current behavior remains unchanged when the provider is not configured.
- The OpenAI path is selected only with explicit configuration.
- The OpenAI path fails early when its key or required assets are missing.
- Unit tests mock the OpenAI client.
- No normal test makes a paid request.
- The final image uses the exact supplied card.
- The original card file is not modified.
- Errors do not produce a misleading final image.
- Optional fallback occurs only when explicitly enabled.
- The scheduled and manual GitHub Actions paths both receive valid defaults.
- Documentation explains local and GitHub configuration.

## Current official references

- OpenAI image-generation guide:
  https://developers.openai.com/api/docs/guides/image-generation
- GPT Image 2 model page:
  https://developers.openai.com/api/docs/models/gpt-image-2
- Image generation API reference:
  https://developers.openai.com/api/reference/resources/images/methods/generate
- Claude Code project instructions:
  https://docs.anthropic.com/en/docs/claude-code/memory
- Claude Code common workflows and file references:
  https://docs.anthropic.com/en/docs/claude-code/common-workflows
