Convert this verified algorithm explanation into a LinkedIn cheat-sheet
content specification. This will be rendered onto a fixed 1080x1350px
canvas by a deterministic renderer — every limit below is a hard
constraint, not a suggestion, because text that doesn't fit will be
truncated or clipped by the renderer's QA gate and fail the run.

You have no tool access in this stage (no Bash, no code execution, no file
access) — this is a pure text-transformation task. Count words/lines by
reading the text directly; do not attempt to run, execute, or shell out to
verify a count. If you're genuinely unsure whether something is under a
limit, trim it further rather than trying to measure it precisely — being
a little short of a limit costs nothing, but attempting to use an
unavailable tool wastes a turn and can fail the run outright.

## Input

The original problem:

```json
{{problem_json}}
```

The verified solution (verify.md's `corrected_code` already merged in if it
set one):

```json
{{verified_solve_json}}
```

## Hard limits

- `headline`: maximum {{headline_max_words}} words. Prefer the pattern
  "Never Forget ..." when it reads naturally (see examples below), but
  don't force it if a different phrasing captures the insight better.
- `problem_summary`: maximum {{problem_summary_max_words}} words.
- `intuition`: maximum {{intuition_max_words}} words.
- `approach`: {{approach_max_steps}} steps max, maximum
  {{approach_max_words_per_step}} words per step.
- `example.states`: maximum {{example_max_states}} entries.
- `complexity`: one line each for time and space, using standard
  Big-O notation.
- `code`: prefer {{code_preferred_max_lines}} visible lines, absolute
  maximum {{code_absolute_max_lines}}. If the verified solution is longer,
  keep the core algorithm and drop non-essential helper functions or
  comments — never drop correctness-relevant logic. Preserve variable
  names from the verified code; do not rename for brevity.
- `diagrams` and `reasoning_panel`: carry through from the input as-is if
  present — trim individual strings only if they exceed the field limits
  in schemas/cheatsheet.schema.json (e.g. a `cell.value` over 12
  characters, a `reasoning_panel.bullets` entry over 120 characters). Do
  not invent a diagram that wasn't in the input, and do not drop one that
  was there unless it no longer fits after your other trims free up room.
  If the input has neither, omit both keys entirely — never set either to
  `null`, the output schema does not accept that.

## Headline style (examples of style only — do not reuse these verbatim)

- Never Forget This Sliding Window Trick
- Never Forget Why n - left Works
- Never Forget This DP Transition
- Never Forget This Binary Search Boundary
- Never Forget the Suffix Trick

Rules: technically meaningful, no hype, no misleading claims, understandable
without reading the code, highlight the specific insight rather than the
problem's topic tag.

## Preserve

- All technical correctness from the verified solution — you are
  compressing wording, not re-deriving the algorithm.
- The key invariant that makes the algorithm work — never cut this even if
  something else has to be trimmed further.
- Remove repetition between fields (e.g. don't restate the headline inside
  the intuition).

## Output

Return ONLY JSON (no prose, no markdown fences) matching
schemas/cheatsheet.schema.json exactly, including the `problem` and
`prompt_version` ("v1") fields, and `diagrams` / `reasoning_panel` if
present, all carried through from the input.
