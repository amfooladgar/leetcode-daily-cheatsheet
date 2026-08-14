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
- **v2** (current default): reworded the branding-reservation section to
  state the given size as a minimum to keep clear, explicitly asking the
  model to err toward more blank space. Paired with
  `image_generation.openai.card_reservation_safety_margin` (default 0.2)
  in `config/settings.yaml`, which pads the *requested* reservation size
  by that fraction — see `src/rendering/card_compositor.py::
  compute_reserved_region()`. The card's actual composited size and
  position are unaffected by either the wording change or the padding.
