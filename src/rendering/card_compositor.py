"""Deterministic visiting-card overlay for the OpenAI provider only (the
existing renderer embeds the card as an HTML <img> data URI in
cheatsheet.html.jinja2 and never touches this module).

Strict invariant (see ARCHITECTURE.md "Optional OpenAI image renderer" and
CLAUDE.md's "contact card" rule): the model is never asked to draw the
card. This module overlays the exact source file -- assets/contact-card.png
-- onto the AI-generated background after generation, and never crops,
redraws, recolors, retouches, sharpens, regenerates, or rewrites it.

Pillow is imported lazily inside each function (same pattern as
src/storage/google_drive.py's google-api-python-client imports) so
`python -m src.main --dry-run` with the default "existing" provider never
requires Pillow to be importable.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


class CardCompositeError(RuntimeError):
    """Raised for any card-compositing failure. Per ARCHITECTURE.md's
    failure policy: if generation succeeded but compositing fails, the
    caller must keep the generated background for diagnosis and must not
    publish it as the final branded output."""


def hash_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_card(card_path: Path) -> None:
    """Fails fast (before any paid OpenAI request) if the card is missing
    or not a decodable image."""
    from PIL import Image

    card_path = Path(card_path)
    if not card_path.exists():
        raise CardCompositeError(f"Visiting card not found at {card_path}")
    try:
        with Image.open(card_path) as img:
            img.verify()
    except Exception as exc:  # noqa: BLE001 - re-raised with context below
        raise CardCompositeError(
            f"Visiting card at {card_path} is not a valid image: {exc}"
        ) from exc


def _fit_box(
    width: int, height: int, available_width: int, available_height: int
) -> tuple[int, int]:
    """Shared proportional-fit core: native size when it already fits
    within (available_width, available_height), otherwise scaled down
    proportionally (never up, never non-proportionally, never cropped)."""
    if available_width <= 0 or available_height <= 0:
        raise CardCompositeError(
            f"No room available to place the card ({available_width}x{available_height}px)."
        )
    if width <= available_width and height <= available_height:
        return width, height
    scale = min(available_width / width, available_height / height)
    return max(1, round(width * scale)), max(1, round(height * scale))


def compute_card_box(
    card_width: int,
    card_height: int,
    *,
    canvas_width: int,
    canvas_height: int,
    margin_right: int,
    margin_bottom: int,
) -> tuple[int, int]:
    """Returns the (width, height) the card would be placed at based only
    on the configured canvas/margins -- native size when it fits within
    the canvas bounds minus margins, otherwise scaled down proportionally.
    Used as the basis compute_reserved_region() pads for prompt sizing.
    Actual compositing prefers detect_blank_region()'s real measurement
    (see composite_card()'s `available_width`/`available_height` params)
    when one is available, since a live smoke test showed GPT Image can
    leave less blank space than either the canvas math or the prompt
    request would suggest."""
    available_width = canvas_width - margin_right
    available_height = canvas_height - margin_bottom
    if available_width <= 0 or available_height <= 0:
        raise CardCompositeError(
            f"Configured margins ({margin_right}px right, {margin_bottom}px bottom) "
            f"leave no room on a {canvas_width}x{canvas_height} canvas."
        )
    return _fit_box(card_width, card_height, available_width, available_height)


def compute_reserved_region(
    card_width: int,
    card_height: int,
    *,
    canvas_width: int,
    canvas_height: int,
    margin_right: int,
    margin_bottom: int,
    safety_margin: float = 0.2,
) -> tuple[int, int]:
    """Returns the (width, height) to *request* as the blank reserved
    region in the generation prompt (src/rendering/openai_prompt.py) --
    the real composited card box from compute_card_box() above, padded by
    `safety_margin` (default 20%) and clamped to the canvas.

    A live smoke test (see CHANGELOG.md) showed GPT Image does not
    reliably honor an exact pixel reservation size: it left a blank region
    shorter than requested, and the card -- placed by composite_card() at
    its real, unpadded size -- overlapped generated content. Requesting
    extra headroom here reduces that risk without changing where or at
    what size the card itself is actually placed; compute_card_box() (and
    therefore composite_card()) is entirely unaffected by this padding."""
    box_width, box_height = compute_card_box(
        card_width,
        card_height,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        margin_right=margin_right,
        margin_bottom=margin_bottom,
    )
    available_width = canvas_width - margin_right
    available_height = canvas_height - margin_bottom
    padded_width = min(available_width, round(box_width * (1 + safety_margin)))
    padded_height = min(available_height, round(box_height * (1 + safety_margin)))
    return padded_width, padded_height


def _sample_positions(start: int, step: int, span: int, count: int, bound: int) -> list[int]:
    """`count` positions evenly spread between `start` and `start + step *
    span`, clamped into [0, bound)."""
    positions = []
    for i in range(1, count + 1):
        frac = i / (count + 1)
        pos = start + step * round(span * frac)
        positions.append(max(0, min(pos, bound - 1)))
    return positions


def detect_blank_region(
    background_path: Path,
    *,
    canvas_width: int,
    canvas_height: int,
    position: str,
    margin_right: int,
    margin_bottom: int,
    max_width: int,
    max_height: int,
    background_hex: str = "#FFFFFF",
    tolerance: int = 12,
    probe_lines: int = 5,
    max_gap: int = 4,
) -> tuple[int, int]:
    """Scans the *generated* background near the configured corner for the
    actual blank rectangle GPT Image left, instead of trusting the size
    requested in the prompt (compute_reserved_region()) -- a live smoke
    test showed the model can leave noticeably less blank space than even
    a padded, "minimum" request (see CHANGELOG.md).

    Anchored at the corner `position` + `margin_right`/`margin_bottom`
    define. Several probe lines are scanned outward from that corner (up
    to `max_width`/`max_height` away -- there's no reason to look further
    than we'd ever actually use) and the *minimum* blank extent found
    across them is returned, so one sustained non-blank run on any single
    line can only shrink the detected region, never inflate it: erring
    toward a smaller, safely-placed card rather than risking overlap.
    Returns (0, 0) if the corner pixel itself isn't blank.

    A run of up to `max_gap` non-blank pixels is tolerated and skipped
    over (counted as still-blank) if genuine blank space resumes right
    after it -- a live smoke test showed a single hairline panel-border
    pixel (~17 units off white) otherwise zeroed out an entire probe line
    even though the real generated background had ample usable blank
    space around it. Only a sustained non-blank run longer than `max_gap`
    is treated as real content.

    `background_hex` (default matches the compositor's own `clear_hex`
    default) is the expected blank/page background color, deliberately
    *not* sampled from the corner pixel itself: if generated content
    extends all the way into the corner, the corner pixel is content, not
    background, and self-sampling would silently treat that content's
    color as "blank"."""
    from PIL import Image

    with Image.open(background_path) as img:
        image = img.convert("RGB")

    if position == "bottom-right":
        anchor_x = canvas_width - margin_right - 1
        x_step = -1
    elif position == "bottom-left":
        anchor_x = margin_right
        x_step = 1
    else:
        raise CardCompositeError(f"Unsupported card_position '{position}'")
    anchor_y = canvas_height - margin_bottom - 1

    background_rgb = _hex_to_rgba(background_hex)[:3]

    def is_blank(rgb) -> bool:
        return all(abs(a - b) <= tolerance for a, b in zip(rgb, background_rgb, strict=True))

    def blank_run(pixel_at, start: int, step: int, bound: int) -> int:
        distance = 0
        pos = start
        while 0 <= pos < bound:
            if is_blank(pixel_at(pos)):
                distance += 1
                pos += step
                continue
            # Not blank -- peek up to max_gap pixels ahead for a thin
            # border/shadow line (e.g. a card panel's outline) with real
            # blank space resuming right after it. A live smoke test
            # showed a single such pixel, ~17 units off white, otherwise
            # zeroed out an entire probe line even though the actual
            # generated background had ample genuine blank space -- see
            # CHANGELOG.md. A short interruption is safe to place a card
            # over; only a sustained non-blank run means real content.
            gap = 0
            probe_pos = pos
            while gap < max_gap and 0 <= probe_pos < bound and not is_blank(pixel_at(probe_pos)):
                gap += 1
                probe_pos += step
            if gap < max_gap and 0 <= probe_pos < bound:
                distance += gap
                pos = probe_pos
                continue
            break
        return distance

    # Probe columns/rows include the anchor line itself plus several more
    # spread across the requested window, so a blank pocket that starts
    # exactly at the corner is measured too, not just its interior.
    probe_xs = [
        anchor_x,
        *_sample_positions(anchor_x, x_step, max_width, probe_lines, canvas_width),
    ]
    detected_height = min(
        blank_run(lambda y, px=px: image.getpixel((px, y)), anchor_y, -1, canvas_height)
        for px in probe_xs
    )

    # Width is only probed within the height we've already confirmed is
    # usable, not the full max_height window -- a live smoke test showed a
    # stray non-blank pixel far outside the height the card will ever
    # actually occupy (because height itself came out smaller than
    # max_height) otherwise zeroed out width for no real reason (see
    # CHANGELOG.md).
    width_probe_span = max(1, min(max_height, detected_height))
    probe_ys = [
        anchor_y,
        *_sample_positions(anchor_y, -1, width_probe_span, probe_lines, canvas_height),
    ]
    detected_width = min(
        blank_run(lambda x, py=py: image.getpixel((x, py)), anchor_x, x_step, canvas_width)
        for py in probe_ys
    )

    return min(detected_width, max_width), min(detected_height, max_height)


def composite_card(
    background_path: Path,
    card_path: Path,
    output_path: Path,
    *,
    canvas_width: int,
    canvas_height: int,
    position: str,
    margin_right: int,
    margin_bottom: int,
    clear_hex: str = "#FFFFFF",
    available_width: int | None = None,
    available_height: int | None = None,
) -> None:
    """Overlays `card_path` onto `background_path`, writing the branded
    final image to `output_path`. Raises CardCompositeError on any
    validation failure. Never modifies `card_path` or `background_path`.

    By default the card is sized via compute_card_box() (canvas bounds
    minus margins only). Pass `available_width`/`available_height`
    together (e.g. from detect_blank_region()'s real measurement of the
    generated background) to size the card against that instead -- the
    card is always placed anchored at the configured corner + margins
    either way, only its maximum size changes."""
    from PIL import Image

    validate_card(card_path)
    before_hash = hash_file(card_path)

    background_path = Path(background_path)
    if not background_path.exists():
        raise CardCompositeError(f"Generated background not found at {background_path}")

    with Image.open(background_path) as bg_img:
        background = bg_img.convert("RGBA")
    if background.size != (canvas_width, canvas_height):
        raise CardCompositeError(
            f"Generated background is {background.size}, expected "
            f"({canvas_width}, {canvas_height})."
        )

    with Image.open(card_path) as card_img:
        card = card_img.convert("RGBA")

    if available_width is not None and available_height is not None:
        box_width, box_height = _fit_box(card.width, card.height, available_width, available_height)
    else:
        box_width, box_height = compute_card_box(
            card.width,
            card.height,
            canvas_width=canvas_width,
            canvas_height=canvas_height,
            margin_right=margin_right,
            margin_bottom=margin_bottom,
        )
    if (box_width, box_height) != card.size:
        # Proportional downscale only, using a high-quality resampler --
        # never crop, recolor, or otherwise alter the source pixels.
        card = card.resize((box_width, box_height), Image.Resampling.LANCZOS)

    if position == "bottom-right":
        x = canvas_width - margin_right - box_width
        y = canvas_height - margin_bottom - box_height
    elif position == "bottom-left":
        x = margin_right
        y = canvas_height - margin_bottom - box_height
    else:
        raise CardCompositeError(f"Unsupported card_position '{position}'")

    x = max(0, min(x, canvas_width - box_width))
    y = max(0, min(y, canvas_height - box_height))
    if box_width > canvas_width or box_height > canvas_height:
        raise CardCompositeError(
            f"Card box ({box_width}x{box_height}) does not fit on the "
            f"{canvas_width}x{canvas_height} canvas even after scaling."
        )

    clear_rgb = Image.new("RGBA", (box_width, box_height), _hex_to_rgba(clear_hex))
    background.paste(clear_rgb, (x, y))
    # Use the card's own alpha channel as the paste mask so transparent
    # source pixels stay transparent instead of being flattened to opaque.
    background.paste(card, (x, y), mask=card.split()[-1])

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    background.convert("RGB").save(tmp_path, format="PNG")
    tmp_path.replace(output_path)

    after_hash = hash_file(card_path)
    if before_hash != after_hash:
        # Should be unreachable (this function never opens card_path for
        # writing) but this is the explicit, tested guarantee the kit
        # requires -- fail loudly rather than silently publish a corrupted
        # source asset state.
        raise CardCompositeError("Visiting card source file hash changed during compositing.")


def _hex_to_rgba(hex_color: str) -> tuple[int, int, int, int]:
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 6:
        r, g, b = (int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
        return (r, g, b, 255)
    if len(hex_color) == 8:
        r, g, b, a = (int(hex_color[i : i + 2], 16) for i in (0, 2, 4, 6))
        return (r, g, b, a)
    raise CardCompositeError(f"Invalid clear_hex color '{hex_color}'")
