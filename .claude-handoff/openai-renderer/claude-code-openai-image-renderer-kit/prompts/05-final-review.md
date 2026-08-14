# Prompt 5: Final pull-request-style review

Review the complete implementation as if it were a pull request.

Focus on:

- backward compatibility;
- centralized provider selection;
- configuration precedence;
- secret handling;
- prompt-injection boundaries;
- OpenAI request and response validation;
- bounded retry behavior;
- file-path and output safety;
- card pixel and aspect-ratio preservation;
- source asset immutability;
- accidental paid calls in tests;
- manual and scheduled GitHub Actions behavior;
- output naming collisions;
- fallback visibility;
- dependency size;
- documentation accuracy;
- maintainability.

Report findings by severity and cite the affected files. Fix only confirmed
issues. Do not perform unrelated stylistic rewrites.

Rerun all relevant checks. Finish with:

- final changed-file list;
- test and static-analysis summary;
- configuration examples for `existing` and `openai`;
- remaining known limitations;
- exact local command for one production run.

Do not commit or push.

