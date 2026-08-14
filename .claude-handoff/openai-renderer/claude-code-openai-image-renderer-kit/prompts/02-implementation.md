# Prompt 2: Implement the approved plan

Implement the approved plan and all requirements in
`MASTER_IMPLEMENTATION_PROMPT.md`.

Important constraints:

- Preserve current behavior and make the current renderer the default.
- Add OpenAI as one optional provider behind a centralized renderer interface.
- Use the official OpenAI SDK and the Image API.
- Default to `gpt-image-2-2026-04-21`, `1536x1024`, high quality, PNG.
- Read `OPENAI_API_KEY` only for the enabled OpenAI path.
- Use `prompts/openai-cheatsheet.txt` as the production prompt template.
- Use the 2213 landscape PNG only as a visual reference or fixture.
- Never ask the model to recreate the visiting card.
- Composite the exact card PNG after generation.
- Mock the OpenAI client in tests; make no paid request.
- Preserve all existing delivery stages.
- Do not commit or push.

Before editing, show the final file-by-file plan based on the repository you
inspected. Then implement it in small, testable steps.

