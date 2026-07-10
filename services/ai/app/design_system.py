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


def _inch(inches: float) -> int:
    """Convert inches to EMU (1 inch = 914400 EMU)."""
    return int(round(inches * 914400))


def get_default_design_system() -> dict:
    """Return the fully-parsed built-in default design system.

    This is the single source of truth consumed by both
    ``app.agent.tools.extract_template_design_system`` (surfaced to the LLM)
    and ``app.renderer.default_template.DefaultTemplateGenerator`` (rendered
    PPTX fallback), so presentations built without a user template stay
    visually coherent with what the renderer actually emits.
    """
    palette_colors = [
        {"name": "primary", "value": "#1F4E78", "type": "hex"},     # professional blue
        {"name": "secondary", "value": "#2980B9", "type": "hex"},   # lighter blue
        {"name": "accent", "value": "#FF7F50", "type": "hex"},      # orange accent
        {"name": "dark", "value": "#15334F", "type": "hex"},        # dark blue
        {"name": "light", "value": "#F0F4F8", "type": "hex"},       # light gray
        {"name": "white", "value": "#FFFFFF", "type": "hex"},
        {"name": "gray", "value": "#808080", "type": "hex"},
    ]

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
    }
