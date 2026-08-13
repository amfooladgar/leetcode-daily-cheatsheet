# [NOT WIRED IN] Optional future visual-illustration prompt

Status: reference only. No code in `src/` calls this. See
ARCHITECTURE.md ("Future: optional visual layer") for why v1 ships without
an AI image-generation step, and what to change if you decide to add one.

If you do wire this in, call it with an OpenAI image model, feeding it only
`cheatsheet["visual_plan"]` and the worked example produced by
`prompts/claude/solve.md` — never the raw code or exact body text. Composite
the result as a background layer underneath the deterministic text/code
layer in `src/rendering/compositor.py`.

---

Create the visual schematic for a technical LeetCode cheat sheet.

The final publication is a LinkedIn portrait post.

DESIGN
- Light mode.
- Clean white or very light neutral background.
- Minimal modern technical style.
- Maximum three accent colors (match config/settings.yaml design.accent_hex).
- Strong information hierarchy.
- Flat vector-like diagram.
- No decorative clutter, no gradients unless extremely subtle.
- No dark background, no photographs, no fake UI, no unnecessary icons.

PURPOSE
The image must make the algorithm visually memorable. Use the supplied
visual_plan to show: important state, transitions, indexes/pointers,
array/tree/graph relationships where applicable, the crucial invariant, and
why the algorithm works.

IMPORTANT
- Do not render long explanatory paragraphs.
- Do not render Python source code.
- Do not invent algorithm steps beyond visual_plan.
- Do not alter any values in the supplied example.
- Leave clean negative space for the deterministic text overlay — the
  compositor will place the headline in the top ~15% and the code block in
  the bottom ~25% of the canvas, so avoid putting essential visual content
  there.

INPUT
{{visual_plan}}
{{worked_example}}
