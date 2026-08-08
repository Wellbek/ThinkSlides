"""
Canonical built-in default design system.

Single source of truth for the design tokens used when no user template is
provided. Both layers consume this so they can never drift apart:

- The agent tool ``extract_template_design_system`` returns this dict to the
  LLM (palette, fonts, slide dimensions, layout catalog, spacing) so the
  model designs slides against the same tokens the renderer will apply.
- The renderer's ``DefaultTemplateGenerator`` derives the fallback PPTX's
  slide dimensions and master palette from this dict.

Keeping these in one place means the LLM's design tokens and the rendered
output always agree - changing a color or dimension here updates both.
"""

from __future__ import annotations
import re

_HEX_COLOR_RE = re.compile(r"^#?[0-9A-Fa-f]{6}$")


def _inch(inches: float) -> int:
    """Convert inches to EMU (1 inch = 914400 EMU)."""
    return int(round(inches * 914400))


# Named palettes a user can pick between when no template was uploaded (so
# this default design system is what actually gets rendered). Each entry
# has the same shape as the default's own palette_colors so it can be
# swapped in directly. Keep names matching the options the system prompt
# tells the LLM to offer via ask_user_question.
COLOR_THEMES: dict[str, list[dict]] = {
    "professional": [
        {"name": "primary", "value": "#1F4E78", "type": "hex"},
        {"name": "secondary", "value": "#2980B9", "type": "hex"},
        {"name": "accent", "value": "#FF7F50", "type": "hex"},
        {"name": "dark", "value": "#15334F", "type": "hex"},
        {"name": "light", "value": "#F0F4F8", "type": "hex"},
        {"name": "white", "value": "#FFFFFF", "type": "hex"},
        {"name": "gray", "value": "#808080", "type": "hex"},
    ],
    "warm": [
        {"name": "primary", "value": "#C0392B", "type": "hex"},
        {"name": "secondary", "value": "#E67E22", "type": "hex"},
        {"name": "accent", "value": "#F1C40F", "type": "hex"},
        {"name": "dark", "value": "#7B241C", "type": "hex"},
        {"name": "light", "value": "#FDEBD0", "type": "hex"},
        {"name": "white", "value": "#FFFFFF", "type": "hex"},
        {"name": "gray", "value": "#808080", "type": "hex"},
    ],
    "monochrome": [
        {"name": "primary", "value": "#2C2C2C", "type": "hex"},
        {"name": "secondary", "value": "#595959", "type": "hex"},
        {"name": "accent", "value": "#8C8C8C", "type": "hex"},
        {"name": "dark", "value": "#141414", "type": "hex"},
        {"name": "light", "value": "#F2F2F2", "type": "hex"},
        {"name": "white", "value": "#FFFFFF", "type": "hex"},
        {"name": "gray", "value": "#B0B0B0", "type": "hex"},
    ],
    "bold": [
        {"name": "primary", "value": "#6C3483", "type": "hex"},
        {"name": "secondary", "value": "#E74C3C", "type": "hex"},
        {"name": "accent", "value": "#F39C12", "type": "hex"},
        {"name": "dark", "value": "#4A235A", "type": "hex"},
        {"name": "light", "value": "#F4ECF7", "type": "hex"},
        {"name": "white", "value": "#FFFFFF", "type": "hex"},
        {"name": "gray", "value": "#808080", "type": "hex"},
    ],
    "nature": [
        {"name": "primary", "value": "#1E8449", "type": "hex"},
        {"name": "secondary", "value": "#52BE80", "type": "hex"},
        {"name": "accent", "value": "#F4D03F", "type": "hex"},
        {"name": "dark", "value": "#145A32", "type": "hex"},
        {"name": "light", "value": "#EAFAF1", "type": "hex"},
        {"name": "white", "value": "#FFFFFF", "type": "hex"},
        {"name": "gray", "value": "#808080", "type": "hex"},
    ],
}
DEFAULT_COLOR_THEME = "professional"


def normalize_color_theme(theme: str | None) -> str:
    """Map any caller-supplied value onto a known theme, defaulting to professional."""
    key = (theme or "").strip().lower()
    return key if key in COLOR_THEMES else DEFAULT_COLOR_THEME


def resolve_palette(color_theme: str | None) -> tuple[str, list[dict]]:
    """Resolve a caller-supplied color_theme to ``(label, palette_colors)``.

    Accepts either one of the named ``COLOR_THEMES`` or a specific brand hex
    (e.g. "#00704A") for when a named preset isn't a real match - "suggest
    Starbucks green" only means something if that exact color can actually
    be applied, not approximated to the nearest preset. A hex color keeps
    every other token (secondary/accent/dark/light/white/gray) from the
    professional theme and only overrides primary, since primary is the
    only slot the renderer currently applies (title text color).
    """
    raw = (color_theme or "").strip()
    if _HEX_COLOR_RE.match(raw):
        hex_value = raw if raw.startswith("#") else f"#{raw}"
        palette = [dict(c) for c in COLOR_THEMES[DEFAULT_COLOR_THEME]]
        for entry in palette:
            if entry["name"] == "primary":
                entry["value"] = hex_value.upper()
        return hex_value.upper(), palette

    theme = normalize_color_theme(raw)
    return theme, COLOR_THEMES[theme]


def get_default_design_system(color_theme: str | None = None) -> dict:
    """Return the fully-parsed built-in default design system.

    This is the single source of truth consumed by both
    ``app.agent.tools.extract_template_design_system`` (surfaced to the LLM)
    and ``app.renderer.default_template.DefaultTemplateGenerator`` (rendered
    PPTX fallback), so presentations built without a user template stay
    visually coherent with what the renderer actually emits.

    ``color_theme`` selects one of ``COLOR_THEMES`` (see there), or a
    specific "#RRGGBB" brand color; an unrecognised value falls back to the
    "professional" palette. See ``resolve_palette``.
    """
    theme_label, palette_colors = resolve_palette(color_theme)

    # Slide dimensions: 10in x 5.625in (16:9), matching the renderer default
    slide_dimensions = {
        "widthEmu": _inch(10),
        "heightEmu": _inch(5.625),
        "widthPx": 960,
        "heightPx": 540,
    }

    layouts = [
        {
            "layoutId": "title",
            "layoutName": "Title Slide",
            "masterId": "1",
            "placeholders": [
                {"idx": 0, "type": "title", "name": "Title",
                 "xEmu": _inch(1.0), "yEmu": _inch(1.5), "cxEmu": _inch(8.0), "cyEmu": _inch(1.5)},
                {"idx": 1, "type": "subtitle", "name": "Subtitle",
                 "xEmu": _inch(1.0), "yEmu": _inch(3.0), "cxEmu": _inch(8.0), "cyEmu": _inch(1.0)},
            ],
            "hasBackground": False,
            "hasDecorations": False,
        },
        {
            "layoutId": "content",
            "layoutName": "Title and Content",
            "masterId": "1",
            "placeholders": [
                {"idx": 0, "type": "title", "name": "Title",
                 "xEmu": _inch(0.6), "yEmu": _inch(0.4), "cxEmu": _inch(9.0), "cyEmu": _inch(1.0)},
                {"idx": 1, "type": "body", "name": "Content",
                 "xEmu": _inch(0.6), "yEmu": _inch(1.5), "cxEmu": _inch(9.0), "cyEmu": _inch(3.5)},
            ],
            "hasBackground": False,
            "hasDecorations": False,
        },
        {
            "layoutId": "twoColumn",
            "layoutName": "Two Column",
            "masterId": "1",
            "placeholders": [
                {"idx": 0, "type": "title", "name": "Title",
                 "xEmu": _inch(0.6), "yEmu": _inch(0.4), "cxEmu": _inch(9.0), "cyEmu": _inch(1.0)},
                {"idx": 1, "type": "body", "name": "Left Column",
                 "xEmu": _inch(0.6), "yEmu": _inch(1.5), "cxEmu": _inch(4.0), "cyEmu": _inch(3.5)},
                {"idx": 2, "type": "body", "name": "Right Column",
                 "xEmu": _inch(5.0), "yEmu": _inch(1.5), "cxEmu": _inch(4.0), "cyEmu": _inch(3.5)},
            ],
            "hasBackground": False,
            "hasDecorations": False,
        },
        {
            "layoutId": "image",
            "layoutName": "Picture with Caption",
            "masterId": "1",
            "placeholders": [
                {"idx": 0, "type": "title", "name": "Title",
                 "xEmu": _inch(0.6), "yEmu": _inch(0.4), "cxEmu": _inch(9.0), "cyEmu": _inch(1.0)},
                {"idx": 1, "type": "image", "name": "Image",
                 "xEmu": _inch(1.0), "yEmu": _inch(1.5), "cxEmu": _inch(4.0), "cyEmu": _inch(3.0)},
                {"idx": 2, "type": "body", "name": "Caption",
                 "xEmu": _inch(5.5), "yEmu": _inch(1.5), "cxEmu": _inch(4.0), "cyEmu": _inch(3.5)},
            ],
            "hasBackground": False,
            "hasDecorations": False,
        },
        {
            "layoutId": "table",
            "layoutName": "Table",
            "masterId": "1",
            "placeholders": [
                {"idx": 0, "type": "title", "name": "Title",
                 "xEmu": _inch(0.6), "yEmu": _inch(0.4), "cxEmu": _inch(9.0), "cyEmu": _inch(1.0)},
                {"idx": 1, "type": "table", "name": "Table",
                 "xEmu": _inch(0.6), "yEmu": _inch(1.5), "cxEmu": _inch(9.0), "cyEmu": _inch(3.5)},
            ],
            "hasBackground": False,
            "hasDecorations": False,
        },
        {
            "layoutId": "conclusion",
            "layoutName": "Conclusion",
            "masterId": "1",
            "placeholders": [
                {"idx": 0, "type": "title", "name": "Title",
                 "xEmu": _inch(1.0), "yEmu": _inch(1.5), "cxEmu": _inch(8.0), "cyEmu": _inch(3.0)},
            ],
            "hasBackground": False,
            "hasDecorations": False,
        },
    ]

    return {
        "palette": {"colors": palette_colors, "schemeName": "thinkslides-default"},
        "typography": {
            "titleFont": {"typeface": "Arial", "sizePt": 36, "bold": True},
            "bodyFont": {"typeface": "Arial", "sizePt": 20, "bold": False},
            "captionFont": {"typeface": "Arial", "sizePt": 12, "bold": False},
        },
        "slideDimensions": slide_dimensions,
        "layouts": layouts,
        "spacing": {
            "slidePaddingEmu": _inch(0.5),
            "elementGapEmu": _inch(0.25),
            "titleTopMarginEmu": _inch(0.3),
            "bodyTopMarginEmu": _inch(0.2),
        },
        "masterElements": [],
        "embeddedMedia": [],
        "sourceType": "default",
        "colorTheme": theme_label,
    }
