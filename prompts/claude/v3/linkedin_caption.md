Draft the non-templated parts of a LinkedIn post caption for this cheat
sheet. `src/main.py`'s `_linkedin_caption()` assembles the final caption
text around what you return here — you are not writing the whole post, only
the fields below.

You have no tool access in this stage (no Bash, no code execution, no file
access) — this is a pure text-transformation task. Do not attempt to run,
execute, or shell out to verify anything.

## Input

The normalized problem (may include `similar_questions`, LeetCode's own
"related problems" data, if it was available for this problem):

```json
{{problem_json}}
```

The compressed cheat-sheet content already rendered onto the card:

```json
{{cheatsheet_json}}
```

## Output fields

- `solution_summary`: 1-3 plain-English sentences describing the approach
  and key insight, written for someone scrolling their LinkedIn feed — not
  a tutorial, no code, no exact numbers you might get wrong (e.g. don't
  invent a specific complexity or benchmark that isn't already stated in
  the input). Base this on `headline`, `key_insight`, and `intuition` from
  the cheat-sheet content.
- `similar_problems`: 0-3 objects `{"title": str, "reason": str}` naming
  other real LeetCode problems that share the same technique or pattern.
  - If `problem_json` includes `similar_questions`, prefer naming problems
    from that list — they are confirmed to exist on LeetCode under that
    exact title.
  - If you name anything not in `similar_questions`, only do so if you are
    genuinely confident it is a real LeetCode problem under that exact
    title — omit the entry entirely rather than invent one. It is always
    better to return fewer (even zero) than to name a problem that doesn't
    exist.
  - `reason` is a short phrase, e.g. "same sliding-window technique", not a
    full sentence.
- `hashtags`: 4-6 strings, each starting with `#`, no spaces. Mix generic
  tags (e.g. `#leetcode`, `#softwareengineering`, `#coding`,
  `#100DaysOfCode`) with 1-2 tailored to `problem_json.topics`.

## Output

Return ONLY JSON (no prose, no markdown fences) matching
schemas/linkedin_caption.schema.json exactly.
