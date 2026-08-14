# Prompt 4: One controlled live smoke test

Run one live OpenAI image-generation smoke test only after confirming that
`OPENAI_API_KEY` is already set in the local environment. Never ask me to paste
the key into chat and never print it.

Use the LeetCode 2213 fixture with:

- provider: `openai`
- model: `gpt-image-2-2026-04-21`
- size: `1536x1024`
- quality: `medium` for this smoke test
- output: PNG
- exactly one image request

Then:

1. Save the generated background.
2. Composite the exact source visiting-card PNG.
3. Report both output paths and dimensions.
4. Confirm the source card hash is unchanged.
5. Inspect the final image for title cropping, broken formulas, unreadable
   pseudocode, content in the reserved card area, or card alteration.
6. Report limitations instead of issuing another paid request.

Do not make a second request, commit, or push without explicit approval.

