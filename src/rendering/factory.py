"""Single centralized dispatch point for image_generation.provider ("existing"
| "openai") -- src/main.py calls only render_cheatsheet_with_provider() and
never branches on provider itself, so provider-specific logic stays out of
main.py, the delivery adapters, and everywhere else (see ARCHITECTURE.md
"Optional OpenAI image renderer").

src/rendering/openai_provider.py is imported lazily, inside the "openai"
branch only, so the default "existing" path never requires the `openai`
package or Pillow to be importable -- same lazy-import discipline as
src/storage/google_drive.py's google-api-python-client imports.
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.rendering.base import RenderResult

log = logging.getLogger(__name__)

_KNOWN_PROVIDERS = ("existing", "openai")


class UnknownProviderError(ValueError):
    """Raised when image_generation.provider (or --image-provider /
    IMAGE_GENERATION_PROVIDER) names a provider this factory doesn't know
    about. Always raised before any paid request."""


def validate_provider(provider: str) -> None:
    if provider not in _KNOWN_PROVIDERS:
        raise UnknownProviderError(
            f"Unknown image_generation provider '{provider}' -- must be one of {_KNOWN_PROVIDERS}"
        )


def render_cheatsheet_with_provider(
    provider: str,
    cheatsheet: dict,
    settings: dict,
    image_path: Path,
    stage_dir: Path,
    *,
    contact_card_path: Path | None,
    fallback_to_existing: bool,
) -> RenderResult:
    """Renders `cheatsheet` with the selected provider. Raises
    UnknownProviderError for an unrecognized provider, or the openai
    provider's OpenAIRenderError if provider="openai" fails and
    fallback_to_existing is False. On a caught openai failure with
    fallback_to_existing=True, logs a warning and falls back to the
    existing renderer -- the openai failure never damages or blocks the
    existing renderer's own path."""
    validate_provider(provider)

    if provider == "existing":
        return _render_existing(cheatsheet, settings, image_path, contact_card_path)

    from src.rendering.openai_provider import OpenAIRenderError
    from src.rendering.openai_provider import render as render_openai

    try:
        return render_openai(cheatsheet, settings, stage_dir, contact_card_path=contact_card_path)
    except OpenAIRenderError as exc:
        if not fallback_to_existing:
            raise
        log.warning(
            "OpenAI renderer failed (%s) -- falling back to the existing renderer "
            "(image_generation.fallback_to_existing=true).",
            exc,
        )
        return _render_existing(cheatsheet, settings, image_path, contact_card_path)


def _render_existing(cheatsheet, settings, image_path, contact_card_path) -> RenderResult:
    from src.rendering.existing_provider import render as render_existing

    return render_existing(cheatsheet, settings, image_path, contact_card_path=contact_card_path)
