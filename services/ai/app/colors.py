"""
Colour maths shared by the design system and the renderer.

Kept out of ``app.design_system`` (which derives palettes) and out of
``app.renderer`` (which applies them) because both need the same helpers and
neither should import the other for them.

Everything here works on ``#RRGGBB`` strings and returns them, so the tokens
in a design system dict stay JSON-serialisable end to end.
"""

from __future__ import annotations
import colorsys
import re

_HEX_RE = re.compile(r"^#?([0-9A-Fa-f]{6})$")

# WCAG AA for normal-size text. Used as the threshold for "is this colour
# legible on that background", not as an aspiration - a title that fails it
# is swapped for one that passes rather than merely warned about.
WCAG_AA_NORMAL = 4.5


def parse_hex(value: str | None) -> tuple[int, int, int] | None:
    """``"#1F4E78"`` -> ``(31, 78, 120)``. None for anything unparseable."""
    match = _HEX_RE.match((value or "").strip())
    if not match:
        return None
    digits = match.group(1)
    return int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16)


def to_hex(rgb: tuple[int, int, int]) -> str:
    r, g, b = (max(0, min(255, int(round(c)))) for c in rgb)
    return f"#{r:02X}{g:02X}{b:02X}"


def _to_hls(hex_value: str) -> tuple[float, float, float] | None:
    rgb = parse_hex(hex_value)
    if rgb is None:
        return None
    r, g, b = (c / 255.0 for c in rgb)
    return colorsys.rgb_to_hls(r, g, b)


def _from_hls(h: float, l: float, s: float) -> str:
    r, g, b = colorsys.hls_to_rgb(h % 1.0, max(0.0, min(1.0, l)), max(0.0, min(1.0, s)))
    return to_hex((r * 255, g * 255, b * 255))


def with_lightness(hex_value: str, lightness: float, saturation_scale: float = 1.0) -> str:
    """Re-light a colour, keeping its hue and (optionally scaled) saturation."""
    hls = _to_hls(hex_value)
    if hls is None:
        return hex_value
    h, _, s = hls
    return _from_hls(h, lightness, s * saturation_scale)


def shift_hue(hex_value: str, degrees: float, lightness: float | None = None,
              min_saturation: float = 0.0) -> str:
    """Rotate a colour's hue, optionally forcing a lightness/saturation floor."""
    hls = _to_hls(hex_value)
    if hls is None:
        return hex_value
    h, l, s = hls
    return _from_hls(
        h + degrees / 360.0,
        l if lightness is None else lightness,
        max(s, min_saturation),
    )


def mix(a: str, b: str, weight: float) -> str:
    """Blend ``weight`` of ``b`` into ``a`` (0.0 -> a, 1.0 -> b)."""
    rgb_a, rgb_b = parse_hex(a), parse_hex(b)
    if rgb_a is None or rgb_b is None:
        return a
    w = max(0.0, min(1.0, weight))
    return to_hex(tuple(ca + (cb - ca) * w for ca, cb in zip(rgb_a, rgb_b)))


def relative_luminance(hex_value: str) -> float:
    """WCAG relative luminance, 0 (black) to 1 (white)."""
    rgb = parse_hex(hex_value)
    if rgb is None:
        return 0.0

    def channel(c: int) -> float:
        srgb = c / 255.0
        return srgb / 12.92 if srgb <= 0.03928 else ((srgb + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: str, b: str) -> float:
    """WCAG contrast ratio between two colours (1.0 to 21.0)."""
    la, lb = relative_luminance(a), relative_luminance(b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


def is_dark(hex_value: str) -> bool:
    """True when text on this background should be light rather than dark."""
    return relative_luminance(hex_value) < 0.4


def readable_on(
    background: str,
    preferred: str,
    fallbacks: tuple[str, ...] = (),
    minimum: float = WCAG_AA_NORMAL,
) -> str:
    """
    The first of ``preferred`` then ``fallbacks`` that clears ``minimum``
    contrast against ``background``; failing all of them, plain white or a
    near-black, whichever wins.

    This is what stops a brand colour being applied blindly: a mid-tone brand
    hex reads fine as a title on white and is close to illegible on the deck's
    own dark section slides, and only the background knows which case it is.
    """
    for candidate in (preferred, *fallbacks):
        if candidate and contrast_ratio(candidate, background) >= minimum:
            return candidate
    return "#FFFFFF" if is_dark(background) else "#1A1A1A"
