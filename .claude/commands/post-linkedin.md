---
description: Draft (or reuse) a LinkedIn caption for a rendered cheat sheet and, only with explicit approval, publish it
---

This command is manual and human-in-the-loop by design (Path B — see
ARCHITECTURE.md "LinkedIn posting"). It must never be called from
`.github/workflows/*`, from `src/main.py`'s unattended run, or chained
automatically from `/solve-daily`. It only runs when you invoke
`/post-linkedin` yourself. This is the feature's actual "off switch": even
if `linkedin.enabled: true`, nothing posts without you approving the exact
caption in this conversation first.

Optional argument: a date (`YYYY-MM-DD`) or `<date> <problem-number>` to
post an older cheat sheet instead of the most recent one. Default: the
newest entry under `output/`.

## 0. Preconditions

1. Read `config/settings.yaml`. If `linkedin.enabled` is not `true`, stop
   and tell me: "LinkedIn posting is disabled (`linkedin.enabled: false` in
   config/settings.yaml). Set it to `true` once LinkedIn OAuth is set up
   (see docs/SETUP.md step 3c) if you want to post." Do not proceed.
2. Resolve the target stage directory: `output/<date>/<problem-number>/`.
   It must contain `cheatsheet.png` and `content.json` — if either is
   missing, say so and stop (I need to run the pipeline for that date
   first, e.g. `python -m src.main --dry-run`).
3. Check `state/manifest.json` for that date/problem. If it already has
   `"linkedin": true`, show me the existing `linkedin_post_urn` and ask
   whether I really want to post the same cheat sheet again before
   continuing (avoid accidental duplicate posts).

## 1. Draft the caption

If `output/<date>/<problem-number>/linkedin_caption.txt` already exists
(Path A, the automatic Telegram now/later prompt in `src/main.py`, may have
already drafted and saved one when I chose "Later" that day), read it and
show it to me as the starting point instead of drafting from scratch — ask
whether I want to use it as-is, edit it, or have you redraft it.

Otherwise, read `content.json` (the compressed cheatsheet content — has
`headline`, `key_insight`, `problem.number/title/difficulty`, etc.) and
`problem.json` (has the LeetCode `url` and, if available,
`similar_questions`). Write a LinkedIn post caption with this shape:

- Hook line: the `headline`, rephrased if needed so it reads naturally as
  the first line of a LinkedIn post (this is the only line visible before
  "...see more").
- 2-4 short sentences: what the problem asks and the key insight/trick
  (`key_insight`, `intuition`) in plain English — no code, no
  implementation detail, written for someone scrolling their feed, not
  someone about to solve it.
- Optionally, 1-3 similar problems worth trying, only ones you're
  genuinely confident are real LeetCode problems by that title (prefer
  `problem.json`'s `similar_questions` if present).
- One line noting the problem number, title, and difficulty, e.g.
  "LeetCode #1 Two Sum (Easy)", then the LeetCode problem URL on its own
  line.
- 4-6 relevant hashtags on the last line (mix of `#leetcode`,
  `#softwareengineering`, `#coding`, `#100DaysOfCode`, and 1-2 tailored to
  the problem's actual topics from `problem.json`'s `topics` field).
- Keep the whole thing well under LinkedIn's 3000-character cap — aim for
  400-800 characters total, since short posts perform better and this is
  a caption for an image, not the full explanation (the image already
  carries the diagram/code).
- Match a confident-but-approachable, first-person tone. No emoji spam
  (0-2 emoji max, only if it fits naturally).

Show me the full caption text and the path to `cheatsheet.png` in the
chat. Do not open or post anything yet.

## 2. Ask for approval

Ask explicitly: "Post this to LinkedIn now?" Wait for my reply.

- If I ask for edits, revise the caption and show it again — don't post
  until I say something unambiguous like "yes", "post it", "go ahead".
- If I decline, stop here. Nothing was posted. This is a normal, expected
  outcome, not an error.

## 3. Publish (only after explicit approval)

1. Write the approved caption to a temp file (avoids shell-escaping a
   multi-paragraph string with hashtags/punctuation).
2. Run:
   ```bash
   python -m scripts.post_to_linkedin \
     --image "output/<date>/<problem-number>/cheatsheet.png" \
     --caption-file <temp caption file> \
     --date <date> \
     --problem-number <problem-number>
   ```
   (`scripts/post_to_linkedin.py` re-checks `linkedin.enabled` itself as a
   second guard, calls `src/storage/linkedin.py`'s `post_cheatsheet()`,
   and — only on success — updates `state/manifest.json`'s `linkedin` /
   `linkedin_post_urn` fields for that date+problem without touching its
   other fields, then prints the published post URL.)
3. Report the result: on success, share the printed
   `https://www.linkedin.com/feed/update/...` URL. On failure, show the
   error verbatim (it will be a `LinkedInPostError` with LinkedIn API's own
   error message) and confirm the local PNG/manifest are unaffected — a
   failed LinkedIn post never deletes or invalidates the rendered cheat
   sheet, and the manifest is left untouched on failure.

## Never do this automatically

Do not add a call to `scripts/post_to_linkedin.py` (or to
`src/storage/linkedin.py`'s `post_cheatsheet()`) into `src/main.py` outside
its own Path A flow (the Telegram now/later prompt, itself gated by
`linkedin.enabled` AND `linkedin.telegram_prompt.enabled` plus a human
button tap), `.github/workflows/daily.yml`, `.github/workflows/ci.yml`, or
`.claude/commands/solve-daily.md`. Both LinkedIn posting paths always
require an explicit human action — a button tap for Path A, your typed
approval here for Path B — before anything reaches LinkedIn.
