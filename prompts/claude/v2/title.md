Generate one memorable educational headline for this algorithm, standalone
(use this only when regenerating just the headline via
`python -m src.main --regenerate-title`, without re-running the full
compress stage).

## Input

```json
{{cheatsheet_json}}
```

## Rules

- Maximum {{headline_max_words}} words total.
- Prefer the pattern "Never Forget ..." when it reads naturally.
- Technically meaningful — a reader who knows the topic should nod, not a
  generic hype phrase that could apply to any problem.
- No clickbait unrelated to the actual solution.
- Understandable without reading the code.
- Highlight the specific insight (e.g. the invariant, the trick), not just
  the problem's topic tag (e.g. not just "Never Forget This DP Problem").

## Output

Return ONLY JSON: `{"headline": "..."}`
