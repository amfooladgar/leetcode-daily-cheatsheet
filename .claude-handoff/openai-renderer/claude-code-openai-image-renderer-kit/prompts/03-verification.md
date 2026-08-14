# Prompt 3: Verify without a paid API call

Verify the implementation without making a real OpenAI request.

1. Run the complete existing test suite.
2. Run the new renderer tests with a mocked OpenAI client.
3. Run the repository's formatter, linter, and type checker.
4. Run the current renderer through its normal local path.
5. Run the OpenAI renderer with fixture Base64 PNG data from the mock.
6. Verify that the final image exists and has the configured dimensions.
7. Verify the card's configured coordinates and aspect ratio.
8. Hash the source card before and after the test and confirm it is unchanged.
9. For native-size insertion, compare the final card region with the source
   card and confirm pixel preservation, accounting only for standard alpha
   compositing where the source itself is transparent.
10. Confirm that the current renderer works without `OPENAI_API_KEY`.
11. Confirm that the OpenAI renderer fails before calling the client when the
    key or card is missing.
12. Confirm that fallback occurs only when enabled.
13. Confirm that no CI or normal test can issue a paid request accidentally.
14. Inspect scheduled and manual GitHub Actions expressions.
15. Show all commands and summarized results.
16. Show `git diff --stat` and a concise list of changed files.

Fix confirmed implementation defects and rerun affected checks. Do not make a
live request, commit, or push.

