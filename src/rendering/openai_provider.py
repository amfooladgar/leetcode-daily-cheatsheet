"""The "openai" image_generation provider: GPT Image generates the complete
visual cheat sheet, including the contact card, via the Images Edit API's
reference-image input.

This is a deliberate, documented exception to "why no AI image model" (see
ARCHITECTURE.md "Optional OpenAI image renderer") -- unlike the `existing`
provider, GPT Image is asked to render exact text/code/formulas as pixels,
which is not deterministic and can misrender. It's an explicit, non-default
opt-in (`image_generation.provider: "openai"` in config/settings.yaml).

Card handling (v3, current default -- see prompts/openai/README.md for the
v1/v2 history this replaced): `assets/contact-card.png` is sent to the
Images Edit API as a reference `image` input, and the prompt
(prompts/openai/v3/cheatsheet.txt) asks the model to draw it directly into
the generated design -- preserving the reference photo exactly
(`input_fidelity: "high"`) while re-lettering the card's text from the
ground-truth `image_generation.openai.card_name`/`card_title`/`card_links`
config values rather than reading the reference image. The source file
itself is only ever opened for reading here, never written to. The earlier
v1/v2 approach (reserve an exact blank rectangle in a text-to-image
request, then Pillow-composite the unmodified source file onto it
afterward) was replaced after two consecutive live runs
(2026-08-16, 2026-08-17) left too little blank space near the reserved
corner for that flow's minimum-scale check to pass, falling back to the
`existing` renderer both days.

The `openai` package is imported lazily inside functions, matching
src/storage/google_drive.py's pattern, so `python -m src.main --dry-run`
with the default "existing" provider never requires it to be importable
and OPENAI_API_KEY is only ever read here.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
from pathlib import Path

from src.rendering.base import RenderResult
from src.rendering.openai_prompt import PromptTemplateError, build_prompt
from src.rendering.png_meta import read_png_size
from src.utils.retry import retry

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_VALID_QUALITIES = {"low", "medium", "high", "auto"}
_VALID_POSITIONS = {"bottom-right", "bottom-left"}
_VALID_INPUT_FIDELITIES = {"low", "high"}
_ENV_VAR = "OPENAI_API_KEY"


class OpenAIRenderError(RuntimeError):
    """Base class for every openai-provider failure. src/rendering/
    factory.py catches this to decide fallback behavior."""


class OpenAIConfigError(OpenAIRenderError):
    """Raised for any failure that must be caught *before* a paid request:
    missing API key, missing prompt template, missing/invalid card,
    invalid size/quality/model/output directory/card text."""


class OpenAIGenerationError(OpenAIRenderError):
    """Raised when the API request or response decoding fails."""


class OpenAIOutputError(OpenAIRenderError):
    """Raised when generation succeeded but its output failed validation
    (wrong dimensions, etc). The caller must keep the generated image for
    diagnosis and must not publish it as final (see ARCHITECTURE.md
    'Failure policy')."""


def _validate_card_file(card_path: Path) -> None:
    """Fails fast (before any paid OpenAI request) if the card is missing
    or not a decodable image."""
    from PIL import Image

    card_path = Path(card_path)
    if not card_path.exists():
        raise OpenAIConfigError(f"Visiting card not found at {card_path}")
    try:
        with Image.open(card_path) as img:
            img.verify()
    except Exception as exc:  # noqa: BLE001 - re-raised with context below
        raise OpenAIConfigError(
            f"Visiting card at {card_path} is not a valid image: {exc}"
        ) from exc


def _validate_card_links(card_links: object) -> list[dict]:
    if not isinstance(card_links, list) or not card_links:
        raise OpenAIConfigError("image_generation.openai.card_links must be a non-empty list")
    for link in card_links:
        if (
            not isinstance(link, dict)
            or not isinstance(link.get("label"), str)
            or not link.get("label")
            or not isinstance(link.get("value"), str)
            or not link.get("value")
        ):
            raise OpenAIConfigError(
                "Each image_generation.openai.card_links entry must have non-empty "
                f"string 'label' and 'value' keys, got {link!r}"
            )
    return card_links


def _validate_config(openai_cfg: dict, stage_dir: Path) -> tuple[int, int, Path]:
    """Fails before any paid request. Returns (width, height, card_path)."""
    width = openai_cfg.get("width")
    height = openai_cfg.get("height")
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        raise OpenAIConfigError(f"Invalid image_generation.openai size: {width}x{height}")

    quality = openai_cfg.get("quality")
    if quality not in _VALID_QUALITIES:
        raise OpenAIConfigError(
            f"Invalid image_generation.openai.quality '{quality}' -- must be one of {sorted(_VALID_QUALITIES)}"
        )

    model = openai_cfg.get("model")
    if not model or not isinstance(model, str):
        raise OpenAIConfigError("image_generation.openai.model must be a non-empty string")

    position = openai_cfg.get("card_position", "bottom-right")
    if position not in _VALID_POSITIONS:
        raise OpenAIConfigError(
            f"Invalid image_generation.openai.card_position '{position}' "
            f"-- must be one of {sorted(_VALID_POSITIONS)}"
        )

    # None (the default) omits the parameter from the API call entirely --
    # a live smoke test showed the configured model rejects it outright
    # (400 invalid_input_fidelity_model) rather than ignoring it, so it's
    # only safe to set for a model confirmed to support it.
    input_fidelity = openai_cfg.get("input_fidelity")
    if input_fidelity is not None and input_fidelity not in _VALID_INPUT_FIDELITIES:
        raise OpenAIConfigError(
            f"Invalid image_generation.openai.input_fidelity '{input_fidelity}' "
            f"-- must be one of {sorted(_VALID_INPUT_FIDELITIES)} or unset"
        )

    if not openai_cfg.get("card_name") or not isinstance(openai_cfg.get("card_name"), str):
        raise OpenAIConfigError("image_generation.openai.card_name must be a non-empty string")
    if not openai_cfg.get("card_title") or not isinstance(openai_cfg.get("card_title"), str):
        raise OpenAIConfigError("image_generation.openai.card_title must be a non-empty string")
    _validate_card_links(openai_cfg.get("card_links"))

    if not os.environ.get(_ENV_VAR):
        raise OpenAIConfigError(
            f"{_ENV_VAR} not set. It is only required when "
            "image_generation.provider is 'openai' -- see docs/SETUP.md."
        )

    prompt_version = openai_cfg.get("prompt_version", "v3")
    from src.rendering.openai_prompt import PROMPTS_DIR

    template_path = PROMPTS_DIR / prompt_version / "cheatsheet.txt"
    if not template_path.exists():
        raise OpenAIConfigError(f"No OpenAI prompt template at {template_path}")

    card_path = REPO_ROOT / openai_cfg["visiting_card"]
    _validate_card_file(card_path)

    try:
        stage_dir = Path(stage_dir)
        stage_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OpenAIConfigError(f"Cannot create output directory {stage_dir}: {exc}") from exc

    return width, height, card_path


def validate_provider_config(settings: dict) -> None:
    """Public entry point for src/main.py to fail fast (before spending any
    Anthropic tokens) if image_generation.provider is 'openai' but its
    configuration is invalid. Uses a throwaway stage_dir check skipped by
    passing REPO_ROOT itself (already exists)."""
    _validate_config(settings["image_generation"]["openai"], REPO_ROOT)


def _decode_image_response(result) -> bytes:
    data = getattr(result, "data", None)
    if not data:
        raise OpenAIGenerationError("OpenAI image response contained no data")
    b64 = getattr(data[0], "b64_json", None)
    if not b64:
        raise OpenAIGenerationError("OpenAI image response contained no b64_json payload")
    try:
        image_bytes = base64.b64decode(b64, validate=True)
    except (ValueError, TypeError) as exc:
        raise OpenAIGenerationError(f"OpenAI image response was not valid base64: {exc}") from exc
    if not image_bytes:
        raise OpenAIGenerationError("Decoded OpenAI image data was empty")
    return image_bytes


def _generate_from_reference(
    *,
    prompt: str,
    model: str,
    size: str,
    quality: str,
    card_path: Path,
    input_fidelity: str | None,
    max_retries: int,
    base_delay_seconds: float,
    max_delay_seconds: float,
) -> bytes:
    import openai as openai_sdk

    client = openai_sdk.OpenAI()

    # Only retry transient failures -- auth/permission/validation errors
    # must fail immediately, never retry (see ARCHITECTURE.md).
    retryable = (
        openai_sdk.RateLimitError,
        openai_sdk.APIConnectionError,
        openai_sdk.InternalServerError,
    )

    @retry(
        exceptions=retryable,
        attempts=max_retries,
        base_delay_seconds=base_delay_seconds,
        max_delay_seconds=max_delay_seconds,
    )
    def _call():
        # Re-opened on every attempt -- a file handle already sent in a
        # prior (failed) request can't be reused for a retry.
        with open(card_path, "rb") as card_file:
            call_kwargs = dict(
                model=model,
                image=card_file,
                prompt=prompt,
                size=size,
                quality=quality,
                n=1,
            )
            # Omitted, not just None, when unset -- passing
            # input_fidelity=None to the SDK still serializes the key
            # (see the 400 finding above).
            if input_fidelity is not None:
                call_kwargs["input_fidelity"] = input_fidelity
            return client.images.edit(**call_kwargs)

    try:
        result = _call()
    except (
        openai_sdk.AuthenticationError,
        openai_sdk.PermissionDeniedError,
        openai_sdk.BadRequestError,
        openai_sdk.NotFoundError,
    ) as exc:
        raise OpenAIGenerationError(
            f"OpenAI image request failed ({type(exc).__name__}): {exc}"
        ) from exc
    except openai_sdk.OpenAIError as exc:
        raise OpenAIGenerationError(f"OpenAI image request failed after retries: {exc}") from exc

    return _decode_image_response(result)


def render(
    cheatsheet: dict, settings: dict, stage_dir: Path, *, contact_card_path: Path | None = None
) -> RenderResult:
    """The openai provider's entry point for src/rendering/factory.py.
    `contact_card_path` is accepted for call-shape parity with
    existing_provider.render() but ignored -- the card path always comes
    from image_generation.openai.visiting_card, per the visiting-card
    invariant (the immutable source asset's location is not something a
    per-run override should change)."""
    stage_dir = Path(stage_dir)
    openai_cfg = settings["image_generation"]["openai"]

    width, height, card_path = _validate_config(openai_cfg, stage_dir)
    card_hash_before = hashlib.sha256(card_path.read_bytes()).hexdigest()

    prompt = build_prompt(
        cheatsheet,
        prompt_version=openai_cfg.get("prompt_version", "v3"),
        card_position=openai_cfg.get("card_position", "bottom-right"),
        card_name=openai_cfg["card_name"],
        card_title=openai_cfg["card_title"],
        card_links=openai_cfg["card_links"],
    )

    try:
        image_bytes = _generate_from_reference(
            prompt=prompt,
            model=openai_cfg["model"],
            size=f"{width}x{height}",
            quality=openai_cfg["quality"],
            card_path=card_path,
            input_fidelity=openai_cfg.get("input_fidelity"),
            max_retries=openai_cfg.get("max_retries", 3),
            base_delay_seconds=openai_cfg.get("retry_base_delay_seconds", 1.0),
            max_delay_seconds=openai_cfg.get("retry_max_delay_seconds", 20.0),
        )
    except PromptTemplateError as exc:
        raise OpenAIConfigError(str(exc)) from exc

    # Written to disk immediately once we have decoded bytes -- this is a
    # billed request, so every image OpenAI actually returns must survive
    # on disk no matter what fails afterward (dimension check, card-hash
    # check, or the caller falling back to the existing renderer). Never
    # skipped, never overwritten by the fallback path (different filename
    # from the existing provider's cheatsheet.png -- see
    # ARCHITECTURE.md "Optional OpenAI image renderer").
    background_path = stage_dir / openai_cfg["background_filename"]
    _write_atomic(background_path, image_bytes)
    log.info("OpenAI background saved to %s", background_path)

    card_hash_after = hashlib.sha256(card_path.read_bytes()).hexdigest()
    if card_hash_before != card_hash_after:
        # Should be unreachable (this function never opens card_path for
        # writing) but this is the explicit, tested guarantee the kit
        # requires -- fail loudly rather than silently publish a corrupted
        # source asset state.
        raise OpenAIOutputError(
            f"Visiting card source file hash changed during generation. "
            f"Generated image kept at {background_path} for diagnosis despite this failure."
        )

    bg_width, bg_height = read_png_size(background_path)
    if (bg_width, bg_height) != (width, height):
        # Generation succeeded but produced the wrong shape -- keep the
        # image for diagnosis, never publish it as final (see
        # ARCHITECTURE.md 'Failure policy').
        raise OpenAIOutputError(
            f"Generated image is {bg_width}x{bg_height}, expected {width}x{height}. "
            f"Kept at {background_path} for diagnosis."
        )

    final_path = stage_dir / openai_cfg["final_filename"]
    _write_atomic(final_path, image_bytes)

    final_width, final_height = read_png_size(final_path)
    checks = {
        "exact_width": final_width == width,
        "exact_height": final_height == height,
        "correct_format": final_path.suffix.lower() == ".png",
        "headline_present": bool(cheatsheet.get("headline")),
    }
    return RenderResult(
        provider="openai",
        passed=all(checks.values()),
        width=final_width,
        height=final_height,
        format="PNG",
        image_path=final_path,
        checks=checks,
        warnings=[],
    )


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_bytes(data)
    tmp_path.replace(path)
