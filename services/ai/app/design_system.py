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

from app import colors

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


def derive_brand_palette(brand_hex: str, accent_hex: str | None = None) -> list[dict]:
    """
    Build a full, internally coherent palette around a single brand colour.

    Every slot is derived from the brand hue rather than inherited from an
    unrelated preset. That inheritance was the bug: asking for Starbucks
    green produced a deck with green titles and *coral* bullet glyphs,
    because only ``primary`` was overridden and ``accent`` stayed at the
    professional theme's ``#FF7F50``. Two unrelated hues on every slide is
    exactly what makes a generated deck read as un-designed.

    The slots and what the renderer does with them:

    * ``primary``   - titles on light slides, table headers, first chart series
    * ``secondary`` - second chart series; a lighter, calmer version of primary
    * ``accent``    - the repeated motif (title rule, bullet glyphs on dark
      slides). Analogous rather than complementary (+32 degrees of hue), which
      stays harmonious for any input hue - a true complement is livelier but
      clashes badly for roughly half the colour wheel, and this runs unattended.
    * ``dark``      - the background of title/section/closing slides, so it has
      to be deep enough for white text to clear WCAG AA.
    * ``light``     - banded table rows and tinted surfaces; a wash of the
      brand hue rather than a neutral grey, so even the quiet areas are on-brand.
    * ``gray``      - footers and slide numbers, hue-tinted just enough to
      belong to the deck without competing with the content.

    ``accent_hex`` sets the highlight role independently of the brand hue,
    which is what a request like "white with slight pink accents" means: a
    light deck whose rules, glyphs and chart series are pink, rather than a
    pink deck or a white one with the pink discarded.
    """
    from app import color_words

    accent = accent_hex or colors.shift_hue(brand_hex, 32, lightness=0.48,
                                            min_saturation=0.55)

    if color_words.is_near_white(brand_hex):
        # The brand colour IS the paper - "white with slight pink accents".
        # Titles cannot be the brand colour here, or the deck is white text
        # on white; the ink is derived from whatever else was named, so it
        # carries a trace of the accent rather than being a flat black
        # dropped in from nowhere.
        ink = colors.with_lightness(accent, 0.14, 0.35 if accent_hex else 0.0)
        return [
            {"name": "primary", "value": ink, "type": "hex"},
            {"name": "secondary", "value": colors.with_lightness(accent, 0.58, 0.75),
             "type": "hex"},
            {"name": "accent", "value": accent, "type": "hex"},
            {"name": "dark", "value": ink, "type": "hex"},
            {"name": "light", "value": colors.with_lightness(accent, 0.955, 0.55),
             "type": "hex"},
            {"name": "white", "value": brand_hex, "type": "hex"},
            {"name": "gray", "value": colors.with_lightness(ink, 0.52, 0.20), "type": "hex"},
        ]

    return [
        {"name": "primary", "value": brand_hex, "type": "hex"},
        # A second chart series follows the accent when one was asked for,
        # so a deck told to highlight in gold does not draw its second bar
        # in a paler version of the first.
        {"name": "secondary",
         "value": colors.with_lightness(accent if accent_hex else brand_hex, 0.45, 0.50),
         "type": "hex"},
        {"name": "accent", "value": accent, "type": "hex"},
        {"name": "dark", "value": colors.with_lightness(brand_hex, 0.15, 0.9), "type": "hex"},
        {"name": "light", "value": colors.with_lightness(brand_hex, 0.955, 0.55), "type": "hex"},
        {"name": "white", "value": "#FFFFFF", "type": "hex"},
        {"name": "gray", "value": colors.with_lightness(brand_hex, 0.52, 0.10), "type": "hex"},
    ]


def resolve_palette(color_theme: str | None) -> tuple[str, list[dict]]:
    """Resolve a caller-supplied color_theme to ``(label, palette_colors)``.

    Accepts either one of the named ``COLOR_THEMES`` or a specific brand hex
    (e.g. "#00704A") for when a named preset isn't a real match - "suggest
    Starbucks green" only means something if that exact color can actually
    be applied, not approximated to the nearest preset. A hex colour derives
    the whole palette around itself (see :func:`derive_brand_palette`).
    """
    raw = (color_theme or "").strip()
    if _HEX_COLOR_RE.match(raw):
        hex_value = (raw if raw.startswith("#") else f"#{raw}").upper()
        return hex_value, derive_brand_palette(hex_value)

    theme = normalize_color_theme(raw)
    return theme, COLOR_THEMES[theme]


def derive_data_viz_config(palette_colors: list[dict], body_font: str = "Arial") -> dict:
    """
    Deterministically derive table/chart styling tokens (header/band colors,
    chart series color sequence, axis/grid colors, font) from an already
    extracted palette - no LLM call needed, matching this module's existing
    "single source of truth, computed once" design.

    Used as the config for the deterministic PPTX/XML template-extraction
    path (which intentionally makes no LLM call at all) and for the built-in
    default design system below. The vision-LLM PDF extraction path
    (extract_template_design_system) asks the model for its own dataViz
    judgment instead, since it can see the actual template, and only falls
    back to this when the model's response doesn't include one.
    """
    by_name = {c.get("name"): c.get("value") for c in palette_colors if c.get("value")}

    # Ordered so the most prominent brand colors are used for the earliest
    # (most visually dominant) data series first.
    series_priority = ["primary", "secondary", "accent", "dark", "gray"]
    color_sequence = [by_name[name] for name in series_priority if name in by_name]
    if not color_sequence:
        color_sequence = [c["value"] for c in palette_colors if c.get("value")][:5]
    if not color_sequence:
        color_sequence = ["#1F4E78", "#2980B9", "#FF7F50", "#808080", "#15334F"]

    return {
        "chartColorSequence": color_sequence,
        "chartAxisColor": by_name.get("dark", "#404040"),
        "chartGridColor": by_name.get("light", "#E0E0E0"),
        "tableHeaderBg": by_name.get("primary", "#1F4E78"),
        "tableHeaderFg": "#FFFFFF",
        "tableBandBg": by_name.get("light", "#F2F2F2"),
        "tableRowBg": "#FFFFFF",
        "tableBorder": by_name.get("gray", "#CCCCCC"),
        "fontFamily": body_font,
        # Style tokens, not just colours. A chart belongs to a deck because of
        # how it is drawn - whether it has an axis, where its labels sit, how
        # thick its bars are - and none of that was recordable before. These
        # are the values the renderer already used, stated rather than
        # hardcoded, so this function's output is unchanged in effect and
        # newly changeable.
        "chartValueAxis": True,
        "chartCategoryAxis": True,
        "chartValueGridlines": True,
        "chartCategoryGridlines": False,
        "chartGridlineWeightPt": 0.7,
        "chartBaselineRule": True,
        "chartLabelMode": "auto",
        "chartLegend": "auto",
        "chartUnitConvention": "axis",
        "chartBarThickness": 0.78,
        "chartBarGroupGap": 0.0,
        # A waterfall's anchor bars and a heatmap's hot end are the deck's own
        # colours, derived here rather than left to the renderer's defaults.
        # The default navy belongs to no deck, which is the same objection this
        # function exists to answer for the series sequence - a purple deck
        # whose bridge totals came out navy is a chart that visibly did not
        # come from it.
        #
        # Rise and fall are deliberately NOT derived. They are semantic: the
        # sign of a movement must not depend on where a deck happened to put
        # its accent colours.
        "chartTotalColor": by_name.get("dark") or color_sequence[0],
        "chartHeatmapHighColor": by_name.get("primary") or color_sequence[0],
        "chartHeatmapLowColor": by_name.get("light", "#F4F6F8"),
        "tableTreatment": "filled",
        "tableRuleWeightPt": 0.75,
        "tableHeaderRuleWeightPt": 1.25,
        "tableHeaderFilled": True,
    }


def _default_chrome() -> dict:
    """
    The deck furniture the built-in design system publishes.

    Imported at call time rather than at module scope: ``renderer.chrome``
    reaches ``renderer.layout``, which reads this module's canvas back out of
    ``get_default_design_system``. A top-level import would close that loop.
    """
    from app.renderer import chrome

    return chrome.default_chrome_declaration()


def _default_apparatus() -> dict:
    """How the built-in design system sets footnotes and the source line."""
    from app.renderer import apparatus

    return apparatus.default_apparatus_tokens()


def _default_density() -> dict:
    """How much the built-in design system expects on a content page."""
    from app.measure.density import default_density

    return default_density()


def _default_typography() -> dict:
    """
    The built-in type scale, with the stacks to try for each face.

    A chain rather than a name: Arial is not installed on every machine that
    renders one of these decks, and a face that cannot be found silently
    becomes whatever the renderer defaults to.
    """
    from app.measure.density import fallback_chain

    sans = fallback_chain("Arial")
    return {
        "titleFont": {"typeface": "Arial", "sizePt": 36, "bold": True,
                      "fallbacks": sans},
        "bodyFont": {"typeface": "Arial", "sizePt": 20, "bold": False,
                     "fallbacks": sans},
        "captionFont": {"typeface": "Arial", "sizePt": 12, "bold": False,
                        "fallbacks": sans},
        "rampPt": [36.0, 20.0, 17.0, 15.0, 12.0],
        "displayIsSerif": False,
    }


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

    # 13.333in x 7.5in - PowerPoint's own 16:9 canvas, not the 10 x 5.625
    # this used to use. Both are 16:9, but a point is an absolute unit: on a
    # 10in-wide slide a 36pt title occupies the same fraction of the page as
    # a 48pt title does on a real one, so every deck rendered a third larger
    # than its own type scale claimed, and slides pasted into a normal deck
    # jumped size. Sizing against the standard canvas is what makes the type
    # scale below mean what it says.
    slide_dimensions = {
        "widthEmu": _inch(13.333),
        "heightEmu": _inch(7.5),
        "widthPx": 1280,
        "heightPx": 720,
    }

    # Geometry, in inches on the 10 x 5.625 canvas. These are the numbers the
    # renderer actually applies to each slide's placeholders (see
    # renderer/deck_theme.py), not an idealised catalog that only the model
    # ever sees - the two drifting apart is what made the model design for a
    # six-layout catalog while everything rendered onto four stock Office
    # layouts.
    TITLE_X, TITLE_W = 0.85, 11.63
    TITLE_Y, TITLE_H = 0.55, 1.05       # bottom-anchored, so the rule sits tight
    RULE_Y, RULE_H, RULE_W = 1.78, 0.06, 1.50
    BODY_Y, BODY_H = 2.10, 4.55         # ends at 6.65, above the footer band
    COL_W, COL_GAP = 5.66, 0.31
    FOOTER_Y, FOOTER_H = 6.85, 0.35
    # Cover/closing slides centre their block instead of hanging it off a title bar.
    HERO_X, HERO_W = 1.15, 11.00
    HERO_TITLE_Y, HERO_TITLE_H = 2.05, 1.65
    HERO_RULE_Y = 3.92
    HERO_SUB_Y, HERO_SUB_H = 4.22, 1.40

    def _ph(idx: int, ph_type: str, name: str,
            x: float, y: float, cx: float, cy: float) -> dict:
        return {"idx": idx, "type": ph_type, "name": name,
                "xEmu": _inch(x), "yEmu": _inch(y),
                "cxEmu": _inch(cx), "cyEmu": _inch(cy)}

    # "role" drives the deck's light/dark rhythm: cover, section breaks and the
    # closing slide are set on the brand's dark tone, content slides on white.
    # A deck where every slide is the same white page is the single biggest
    # reason generated output reads as un-designed.
    layouts = [
        {
            "layoutId": "title",
            "layoutName": "Title Slide",
            "masterId": "1",
            "role": "cover",
            "placeholders": [
                _ph(0, "title", "Title", HERO_X, HERO_TITLE_Y, HERO_W, HERO_TITLE_H),
                _ph(1, "subtitle", "Subtitle", HERO_X, HERO_SUB_Y, HERO_W, HERO_SUB_H),
            ],
            "hasBackground": True,
            "hasDecorations": True,
        },
        {
            "layoutId": "section",
            "layoutName": "Section Header",
            "masterId": "1",
            "role": "section",
            "placeholders": [
                _ph(0, "title", "Section Title", HERO_X, HERO_TITLE_Y, HERO_W, HERO_TITLE_H),
                _ph(1, "body", "Section Note", HERO_X, HERO_SUB_Y, HERO_W, HERO_SUB_H),
            ],
            "hasBackground": True,
            "hasDecorations": True,
        },
        {
            "layoutId": "content",
            "layoutName": "Title and Content",
            "masterId": "1",
            "role": "content",
            "placeholders": [
                _ph(0, "title", "Title", TITLE_X, TITLE_Y, TITLE_W, TITLE_H),
                _ph(1, "body", "Content", TITLE_X, BODY_Y, TITLE_W, BODY_H),
            ],
            "hasBackground": True,
            "hasDecorations": True,
        },
        {
            "layoutId": "twoColumn",
            "layoutName": "Two Column",
            "masterId": "1",
            "role": "content",
            "placeholders": [
                _ph(0, "title", "Title", TITLE_X, TITLE_Y, TITLE_W, TITLE_H),
                _ph(1, "body", "Left Column", TITLE_X, BODY_Y, COL_W, BODY_H),
                _ph(2, "body", "Right Column",
                    TITLE_X + COL_W + COL_GAP, BODY_Y, COL_W, BODY_H),
            ],
            "hasBackground": True,
            "hasDecorations": True,
        },
        {
            "layoutId": "image",
            "layoutName": "Picture with Caption",
            "masterId": "1",
            "role": "content",
            "placeholders": [
                _ph(0, "title", "Title", TITLE_X, TITLE_Y, TITLE_W, TITLE_H),
                _ph(1, "image", "Image", TITLE_X, BODY_Y, COL_W, BODY_H),
                _ph(2, "body", "Caption",
                    TITLE_X + COL_W + COL_GAP, BODY_Y, COL_W, BODY_H),
            ],
            "hasBackground": True,
            "hasDecorations": True,
        },
        {
            "layoutId": "table",
            "layoutName": "Table",
            "masterId": "1",
            "role": "content",
            "placeholders": [
                _ph(0, "title", "Title", TITLE_X, TITLE_Y, TITLE_W, TITLE_H),
                _ph(1, "table", "Table", TITLE_X, BODY_Y, TITLE_W, BODY_H),
            ],
            "hasBackground": True,
            "hasDecorations": True,
        },
        {
            # Without this entry, a slide the planner marked contentType
            # "chart" had nothing in the catalog to match and fell back to
            # whatever select_layout's keyword search hit first.
            "layoutId": "chart",
            "layoutName": "Chart",
            "masterId": "1",
            "role": "content",
            "placeholders": [
                _ph(0, "title", "Title", TITLE_X, TITLE_Y, TITLE_W, TITLE_H),
                _ph(1, "chart", "Chart", TITLE_X, BODY_Y, TITLE_W, BODY_H),
            ],
            "hasBackground": True,
            "hasDecorations": True,
        },
        {
            "layoutId": "conclusion",
            "layoutName": "Conclusion",
            "masterId": "1",
            "role": "closing",
            "placeholders": [
                _ph(0, "title", "Title", HERO_X, HERO_TITLE_Y, HERO_W, HERO_TITLE_H),
                _ph(1, "body", "Closing Note", HERO_X, HERO_SUB_Y, HERO_W, HERO_SUB_H),
            ],
            "hasBackground": True,
            "hasDecorations": True,
        },
    ]

    return {
        "palette": {"colors": palette_colors, "schemeName": "thinkslides-default"},
        "typography": _default_typography(),
        "slideDimensions": slide_dimensions,
        "layouts": layouts,
        "spacing": {
            "slidePaddingEmu": _inch(0.5),
            "elementGapEmu": _inch(0.25),
            "titleTopMarginEmu": _inch(0.3),
            "bodyTopMarginEmu": _inch(0.2),
            # The repeated motif and the footer band. The renderer reads these
            # rather than carrying its own copy, so a change here moves the
            # actual rendered decoration.
            "accentRule": {
                "xEmu": _inch(TITLE_X), "yEmu": _inch(RULE_Y),
                "cxEmu": _inch(RULE_W), "cyEmu": _inch(RULE_H),
            },
            "heroAccentRule": {
                "xEmu": _inch(HERO_X), "yEmu": _inch(HERO_RULE_Y),
                "cxEmu": _inch(RULE_W), "cyEmu": _inch(RULE_H),
            },
            "footer": {
                "xEmu": _inch(TITLE_X), "yEmu": _inch(FOOTER_Y),
                "cxEmu": _inch(TITLE_W), "cyEmu": _inch(FOOTER_H),
                "sizePt": 11,
            },
        },
        "masterElements": [],
        "embeddedMedia": [],
        # Deck furniture, declared rather than hardcoded in the renderer, so a
        # design direction can move it or drop it. The defaults reproduce the
        # footer band this renderer drew before chrome was declarable.
        "chrome": _default_chrome(),
        # How a slide sets its own footnotes and source line. Separate from
        # the footer band: a footnote resolving a marker in a chart label is
        # not the same instrument as a deck-wide reference list.
        "apparatus": _default_apparatus(),
        # How much goes on a page. Carried as a token like any other, because
        # a deck's density is as much a part of how it reads as its palette,
        # and without a field for it an uploaded dense template loses it.
        "density": _default_density(),
        "sourceType": "default",
        "colorTheme": theme_label,
        "dataViz": derive_data_viz_config(palette_colors, body_font="Arial"),
    }
