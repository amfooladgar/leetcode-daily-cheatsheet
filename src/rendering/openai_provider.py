"""The "openai" image_generation provider: GPT Image generates the complete
visual cheat sheet, then src/rendering/card_compositor.py overlays the
exact assets/contact-card.png afterward.

This is a deliberate, documented exception to "why no AI image model" (see
ARCHITECTURE.md "Optional OpenAI image renderer") -- unlike the `existing`
provider, GPT Image is asked to render exact text/code/formulas as pixels,
which is not deterministic and can misrender. It's an explicit, non-default
opt-in (`image_generation.provider: "openai"` in config/settings.yaml).

The `openai` package and Pillow (via card_compositor) are imported lazily
inside functions, matching src/storage/google_drive.py's pattern, so
`python -m src.main --dry-run` with the default "existing" provider never
requires either to be importable and OPENAI_API_KEY is only ever read here.
"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path

from src.rendering.base import RenderResult
from src.rendering.card_compositor import (
    CardCompositeError,
    composite_card,
    compute_reserved_region,
    detect_blank_region,
    validate_card,
)
from src.rendering.openai_prompt import PromptTemplateError, build_prompt
from src.rendering.png_meta import read_png_size
from src.utils.retry import retry

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_VALID_QUALITIES = {"low", "medium", "high", "auto"}
_ENV_VAR = "OPENAI_API_KEY"


class OpenAIRenderError(RuntimeError):
    """Base class for every openai-provider failure. src/rendering/
    factory.py catches this to decide fallback behavior."""


class OpenAIConfigError(OpenAIRenderError):
    """Raised for any failure that must be caught *before* a paid request:
    missing API key, missing prompt template, missing/invalid card,
    invalid size/quality/model/output directory."""


class OpenAIGenerationError(OpenAIRenderError):
    """Raised when the API request or response decoding fails."""


class OpenAICompositeError(OpenAIRenderError):
    """Raised when generation succeeded but card compositing failed. The
    caller must keep the generated background for diagnosis and must not
    publish it as final (see ARCHITECTURE.md 'Failure policy')."""


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

    safety_margin = openai_cfg.get("card_reservation_safety_margin", 0.2)
    if not isinstance(safety_margin, int | float) or safety_margin < 0:
        raise OpenAIConfigError(
            f"Invalid image_generation.openai.card_reservation_safety_margin '{safety_margin}' "
            "-- must be a number >= 0"
        )

    min_scale = openai_cfg.get("card_min_detected_scale", 0.4)
    if not isinstance(min_scale, int | float) or not (0 < min_scale <= 1):
        raise OpenAIConfigError(
            f"Invalid image_generation.openai.card_min_detected_scale '{min_scale}' "
            "-- must be a number in (0, 1]"
        )

    if not os.environ.get(_ENV_VAR):
        raise OpenAIConfigError(
            f"{_ENV_VAR} not set. It is only required when "
            "image_generation.provider is 'openai' -- see docs/SETUP.md."
        )

    prompt_version = openai_cfg.get("prompt_version", "v2")
    from src.rendering.openai_prompt import PROMPTS_DIR

    template_path = PROMPTS_DIR / prompt_version / "cheatsheet.txt"
    if not template_path.exists():
        raise OpenAIConfigError(f"No OpenAI prompt template at {template_path}")

    card_path = REPO_ROOT / openai_cfg["visiting_card"]
    try:
        validate_card(card_path)
    except CardCompositeError as exc:
        raise OpenAIConfigError(str(exc)) from exc

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


def _generate_background(
    *,
    prompt: str,
    model: str,
    size: str,
    quality: str,
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
        return client.images.generate(model=model, prompt=prompt, size=size, quality=quality, n=1)

    try:
        result = _call()
    except (
        openai_sdk.AuthenticationError,
        openai_sdk.PermissionDeniedError,
        openai_sdk.BadRequestError,
        openai_sdk.NotFoundError,
    ) as exc:
        raise OpenAIGenerationError(f"OpenAI image request failed ({type(exc).__name__}): {exc}") from exc
    except openai_sdk.OpenAIError as exc:
        raise OpenAIGenerationError(f"OpenAI image request failed after retries: {exc}") from exc

    return _decode_image_response(result)


def render(cheatsheet: dict, settings: dict, stage_dir: Path, *, contact_card_path: Path | None = None) -> RenderResult:
    """The openai provider's entry point for src/rendering/factory.py.
    `contact_card_path` is accepted for call-shape parity with
    existing_provider.render() but ignored -- the card path always comes
    from image_generation.openai.visiting_card, per the visiting-card
    invariant (the immutable source asset's location is not something a
    per-run override should change)."""
    stage_dir = Path(stage_dir)
    openai_cfg = settings["image_generation"]["openai"]

    width, height, card_path = _validate_config(openai_cfg, stage_dir)
    card_native_width, card_native_height = read_png_size(card_path)

    # The card is composited later at its exact, unpadded size (see
    # composite_card() below, which recomputes compute_card_box() itself).
    # What we request the model *reserve* is padded by
    # card_reservation_safety_margin -- see compute_reserved_region()'s
    # docstring for why (a live smoke test showed GPT Image under-reserves
    # relative to an exact pixel request).
    reserved_width, reserved_height = compute_reserved_region(
        card_native_width,
        card_native_height,
        canvas_width=width,
        canvas_height=height,
        margin_right=openai_cfg["card_margin_right"],
        margin_bottom=openai_cfg["card_margin_bottom"],
        safety_margin=openai_cfg.get("card_reservation_safety_margin", 0.2),
    )

    prompt = build_prompt(
        cheatsheet,
        prompt_version=openai_cfg.get("prompt_version", "v2"),
        card_width=reserved_width,
        card_height=reserved_height,
        card_margin_right=openai_cfg["card_margin_right"],
        card_margin_bottom=openai_cfg["card_margin_bottom"],
    )

    try:
        image_bytes = _generate_background(
            prompt=prompt,
            model=openai_cfg["model"],
            size=f"{width}x{height}",
            quality=openai_cfg["quality"],
            max_retries=openai_cfg.get("max_retries", 3),
            base_delay_seconds=openai_cfg.get("retry_base_delay_seconds", 1.0),
            max_delay_seconds=openai_cfg.get("retry_max_delay_seconds", 20.0),
        )
    except PromptTemplateError as exc:
        raise OpenAIConfigError(str(exc)) from exc

    background_path = stage_dir / openai_cfg["background_filename"]
    _write_atomic(background_path, image_bytes)
    log.info("OpenAI background saved to %s", background_path)

    bg_width, bg_height = read_png_size(background_path)
    if (bg_width, bg_height) != (width, height):
        # Generation succeeded but produced the wrong shape -- keep the
        # background for diagnosis, never publish it as final (see
        # ARCHITECTURE.md 'Failure policy').
        raise OpenAICompositeError(
            f"Generated background is {bg_width}x{bg_height}, expected {width}x{height}. "
            f"Background kept at {background_path} for diagnosis."
        )

    # Don't trust the requested reservation size -- measure what's
    # actually blank in the generated background and fit the card to that
    # (see detect_blank_region()'s docstring: the prompt-requested size,
    # even padded, is not reliable). Capped at the padded reservation so
    # detection never reports more room than we asked the model for.
    detected_width, detected_height = detect_blank_region(
        background_path,
        canvas_width=width,
        canvas_height=height,
        position=openai_cfg["card_position"],
        margin_right=openai_cfg["card_margin_right"],
        margin_bottom=openai_cfg["card_margin_bottom"],
        max_width=reserved_width,
        max_height=reserved_height,
        background_hex=openai_cfg.get("card_clear_hex", "#FFFFFF"),
    )
    min_scale = openai_cfg.get("card_min_detected_scale", 0.4)
    detected_scale = min(detected_width / card_native_width, detected_height / card_native_height)
    if detected_scale < min_scale:
        raise OpenAICompositeError(
            f"Generated background left only {detected_width}x{detected_height}px of blank space "
            f"near the {openai_cfg['card_position']} corner -- too small to place the "
            f"{card_native_width}x{card_native_height}px card at >= {min_scale:.0%} scale. "
            f"Background kept at {background_path} for diagnosis; final image not published."
        )

    final_path = stage_dir / openai_cfg["final_filename"]
    try:
        composite_card(
            background_path,
            card_path,
            final_path,
            canvas_width=width,
            canvas_height=height,
            position=openai_cfg["card_position"],
            margin_right=openai_cfg["card_margin_right"],
            margin_bottom=openai_cfg["card_margin_bottom"],
            clear_hex=openai_cfg.get("card_clear_hex", "#FFFFFF"),
            available_width=detected_width,
            available_height=detected_height,
        )
    except CardCompositeError as exc:
        raise OpenAICompositeError(
            f"{exc} Background kept at {background_path} for diagnosis; final image not published."
        ) from exc

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
