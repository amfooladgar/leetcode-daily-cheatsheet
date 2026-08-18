# OpenAI image-generation prompts

Same versioning convention as `prompts/claude/README.md`: each file here is
passed (with `{{placeholders}}` substituted by
`src/rendering/openai_prompt.py`) as the prompt to the OpenAI Image API.
`src/rendering/openai_provider.py` takes a `prompt_version` argument
(default `"v1"`, set in `config/settings.yaml`'s
`image_generation.openai.prompt_version`) and reads
`prompts/openai/{prompt_version}/cheatsheet.txt`.

Unlike `prompts/claude/`, this prompt does not ask an LLM to reason about
the problem -- it asks an image model (GPT Image) to render the *already
verified* `schemas/cheatsheet.schema.json` content as a complete visual
cheat sheet. See ARCHITECTURE.md "Optional OpenAI image renderer" for why
this is an explicit, non-default opt-in (GPT Image can misrender exact
text/code/formulas -- the `existing` HTML/CSS renderer cannot).

## Versioning

If you materially change what this prompt asks for (not just wording),
copy `prompts/openai/v1/` to `prompts/openai/v2/` and bump
`image_generation.openai.prompt_version` in `config/settings.yaml`.

- **v1**: initial version. A live smoke test showed GPT Image does not
  reliably honor an exact-pixel branding reservation -- it left less blank
  space than the requested `card_width`/`card_height`, and the card
  (composited afterward at its real, unpadded size) overlapped generated
  content.
- **v2**: reworded the branding-reservation section to state the given
  size as a minimum to keep clear, explicitly asking the model to err
  toward more blank space. Paired with a now-removed
  `image_generation.openai.card_reservation_safety_margin` (default 0.2)
  in `config/settings.yaml`, which padded the *requested* reservation
  size by that fraction. The card's actual composited size and position
  were unaffected by either the wording change or the padding. Retired
  after two consecutive live runs (2026-08-16, 2026-08-17) each left too
  little real blank space near the reserved corner for the post-hoc
  blank-region scan to accept, falling back to the `existing` renderer
  both days — see ARCHITECTURE.md "Optional OpenAI image renderer".
- **v3** (current default): replaces the "reserve blank space, composite
  the exact card afterward" flow entirely. `assets/contact-card.png` is
  sent to the Images Edit API as a reference `image` input, and the new
  CONTACT CARD section asks the model to draw the card directly into the
  generated design, re-lettering the card's text from the ground-truth
  `card_name`/`card_title`/`card_links` config values instead of reading
  them off the reference image. `image_generation.openai.input_fidelity`
  can additionally ask the Images Edit API to preserve the reference
  photo's fine detail, but is left unset by default -- a live smoke test
  showed the configured model rejects that parameter outright (see
  ARCHITECTURE.md); a separate live smoke test confirmed the model
  preserves the reference photo faithfully from the prompt instructions
  alone even without it. There is no post-generation compositing step;
  the model's output is the final image. `src/rendering/
  openai_provider.py`'s `build_prompt()` still accepts v1/v2's
  `card_width`/`card_height`/`card_margin_right`/`card_margin_bottom`
  keyword args for backward compatibility (harmless no-ops against a v3
  template, which doesn't reference those placeholders).
