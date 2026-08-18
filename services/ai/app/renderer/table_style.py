"""
Resolves table and chart styling from a run's design system, so generated
tables/charts pick up the template's own conventions instead of a generic
default - e.g. a Starbucks-templated deck gets a Starbucks-green table
header and chart series automatically.

Colours are the smallest part of why a chart looks like it belongs to a deck.
The reference deck's charts are recognisable because of decisions that were
not recorded here at all: page 22's bar chart has **no y-axis and no
gridlines**, and a data label at the end of every bar instead. Page 110's line
chart labels only its final point. Both panels put the unit on a second
subtitle line rather than on an axis. Meanwhile the renderer drew a gridded
y-axis on every non-pie chart unconditionally - so a user could upload a
template whose charts have no axes and get charts with axes and gridlines in
their brand colours.

So ``dataViz`` carries a **style token set** rather than a colour bag, and the
renderer reads it rather than hardcoding. Every token defaults to what was
drawn before, which is what makes this additive: a deck that declares nothing
renders exactly as it did.

Prefers an explicit ``dataViz`` config (written once during design-system
extraction - see extract_template_design_system / design_system.py's
derive_data_viz_config) and falls back to deriving reasonable values from the
base ``palette`` when it is absent, so rendering does not hard-depend on that
extraction step having populated it.
"""

from __future__ import annotations
from dataclasses import dataclass, field as dc_field
from typing import Any

DEFAULT_HEADER_BG = "#1F4E79"
DEFAULT_HEADER_FG = "#FFFFFF"
DEFAULT_BAND_BG = "#F2F2F2"
DEFAULT_ROW_BG = "#FFFFFF"
DEFAULT_BORDER = "#CCCCCC"
DEFAULT_FONT = "Arial"
DEFAULT_AXIS_COLOR = "#404040"
DEFAULT_GRID_COLOR = "#E0E0E0"
DEFAULT_CHART_COLOR_SEQUENCE = ["#1F4E78", "#2980B9", "#FF7F50", "#808080", "#15334F"]

# Where data labels go. "auto" is what the renderer already did: label every
# point when there are few enough of them to stay readable.
LABEL_MODES = frozenset({"auto", "none", "all", "terminal", "end"})
# How a legend is drawn.
LEGEND_STYLES = frozenset({"auto", "none", "swatches", "inline"})
# Where the unit is stated.
UNIT_CONVENTIONS = frozenset({"axis", "subtitle", "title"})
# How a table separates its rows.
TABLE_TREATMENTS = frozenset({"filled", "hairline"})


@dataclass
class TableStyle:
    header_bg: str
    header_fg: str
    band_bg: str
    row_bg: str
    border: str
    font_name: str
    # Beyond colours: whether rows are filled bands or separated by hairlines,
    # how heavy those rules are, and whether the header is a filled bar or a
    # ruled line. Reference pages 12 and 40 are hairline tables, and rendering
    # them as filled bands is most of why a generated table does not look like
    # the template it came from.
    treatment: str = "filled"
    rule_weight_pt: float = 0.75
    header_rule_weight_pt: float = 1.25
    header_filled: bool = True
    # The stack to try when ``font_name`` is not installed where we render.
    font_fallbacks: list[str] = dc_field(default_factory=list)


@dataclass
class ChartStyle:
    color_sequence: list[str]
    axis_color: str
    grid_color: str
    font_name: str

    # Axis and gridline behaviour, per orientation. The single biggest visual
    # difference between our charts and the reference's.
    show_value_axis: bool = True
    show_category_axis: bool = True
    show_value_gridlines: bool = True
    show_category_gridlines: bool = False
    gridline_weight_pt: float = 0.7
    baseline_rule: bool = True

    # Data labels.
    label_mode: str = "auto"
    label_format: str = "auto"
    unit_suffix: str = ""

    # Legend and unit conventions.
    legend_style: str = "auto"
    unit_convention: str = "axis"

    # Bar geometry.
    bar_thickness: float = 0.78
    bar_group_gap: float = 0.0

    # A figure printed inside a stacked band sits on that band's own colour,
    # not on the slide, so it is the one label that cannot take axis_color.
    # None means "work it out from the band" at draw time.
    stack_label_color: str | None = None

    # A waterfall says its content with colour: a rise, a fall, and the two
    # totals it bridges between. Semantic rather than sequential - taking
    # these from the palette's first three would make the sign of a movement
    # depend on where the deck happened to put its accent colours.
    waterfall_rise_color: str = "#2E9AA6"
    waterfall_fall_color: str = "#C0553B"
    waterfall_total_color: str = "#12233F"

    # A heatmap's ramp runs between these. Built from the deck's own accent
    # rather than a library colormap: matplotlib's defaults are excellent and
    # belong to no deck, and a heatmap in someone else's palette is the most
    # obvious way a generated exhibit announces that it was generated.
    # heatmap_high_color of None means "use the palette's first colour".
    heatmap_low_color: str = "#F4F6F8"
    heatmap_high_color: str | None = None

    # The stack to try when ``font_name`` is not installed where we render.
    # A chart is rasterised on this host, so unlike the PPTX's own text it
    # cannot leave the substitution to the machine that opens the deck - and
    # a serif deck whose charts come back in DejaVu Sans is a mismatch a
    # reader sees immediately.
    font_fallbacks: list[str] = dc_field(default_factory=list)

    def labels_visible(self, point_count: int, max_points: int) -> bool:
        """Whether any data label is drawn at all."""
        if self.label_mode == "none":
            return False
        if self.label_mode in ("all", "terminal", "end"):
            return True
        return point_count <= max_points


def resolve_table_style(design_system: dict | None) -> TableStyle:
    design_system = design_system or {}
    data_viz = design_system.get("dataViz") or {}

    if data_viz.get("tableHeaderBg"):
        style = TableStyle(
            header_bg=data_viz.get("tableHeaderBg", DEFAULT_HEADER_BG),
            header_fg=data_viz.get("tableHeaderFg", DEFAULT_HEADER_FG),
            band_bg=data_viz.get("tableBandBg", DEFAULT_BAND_BG),
            row_bg=data_viz.get("tableRowBg", DEFAULT_ROW_BG),
            border=data_viz.get("tableBorder", DEFAULT_BORDER),
            font_name=data_viz.get("fontFamily", DEFAULT_FONT),
        )
    else:
        palette = ((design_system.get("palette") or {}).get("colors")) or []
        primary = next(
            (c.get("value") for c in palette if c.get("name") == "primary"), None
        )
        body_font = (((design_system.get("typography") or {}).get("bodyFont"))
                     or {}).get("typeface")
        style = TableStyle(
            header_bg=primary or DEFAULT_HEADER_BG,
            header_fg=DEFAULT_HEADER_FG,
            band_bg=DEFAULT_BAND_BG,
            row_bg=DEFAULT_ROW_BG,
            border=DEFAULT_BORDER,
            font_name=body_font or DEFAULT_FONT,
        )

    style.treatment = _one_of(data_viz.get("tableTreatment"), TABLE_TREATMENTS,
                              style.treatment)
    style.rule_weight_pt = _positive(data_viz.get("tableRuleWeightPt"),
                                     style.rule_weight_pt)
    style.header_rule_weight_pt = _positive(data_viz.get("tableHeaderRuleWeightPt"),
                                            style.header_rule_weight_pt)
    style.header_filled = _flag(data_viz.get("tableHeaderFilled"),
                                style.header_filled)
    style.font_fallbacks = font_chain(design_system, style.font_name)
    return style


def resolve_chart_style(design_system: dict | None) -> ChartStyle:
    design_system = design_system or {}
    data_viz = design_system.get("dataViz") or {}

    if data_viz.get("chartColorSequence"):
        style = ChartStyle(
            color_sequence=list(data_viz["chartColorSequence"]),
            axis_color=data_viz.get("chartAxisColor", DEFAULT_AXIS_COLOR),
            grid_color=data_viz.get("chartGridColor", DEFAULT_GRID_COLOR),
            font_name=data_viz.get("fontFamily", DEFAULT_FONT),
        )
    else:
        palette = ((design_system.get("palette") or {}).get("colors")) or []
        sequence = [c.get("value") for c in palette if c.get("value")][:5]
        body_font = (((design_system.get("typography") or {}).get("bodyFont"))
                     or {}).get("typeface")
        style = ChartStyle(
            color_sequence=sequence or list(DEFAULT_CHART_COLOR_SEQUENCE),
            axis_color=DEFAULT_AXIS_COLOR,
            grid_color=DEFAULT_GRID_COLOR,
            font_name=body_font or DEFAULT_FONT,
        )

    # Every token below defaults to what the renderer drew before it was a
    # token, so a design system that declares none of them is unchanged.
    style.show_value_axis = _flag(data_viz.get("chartValueAxis"),
                                  style.show_value_axis)
    style.show_category_axis = _flag(data_viz.get("chartCategoryAxis"),
                                     style.show_category_axis)
    style.show_value_gridlines = _flag(data_viz.get("chartValueGridlines"),
                                       style.show_value_gridlines)
    style.show_category_gridlines = _flag(data_viz.get("chartCategoryGridlines"),
                                          style.show_category_gridlines)
    style.gridline_weight_pt = _positive(data_viz.get("chartGridlineWeightPt"),
                                         style.gridline_weight_pt)
    style.baseline_rule = _flag(data_viz.get("chartBaselineRule"),
                                style.baseline_rule)
    style.label_mode = _one_of(data_viz.get("chartLabelMode"), LABEL_MODES,
                               style.label_mode)
    style.label_format = str(data_viz.get("chartLabelFormat")
                             or style.label_format)
    style.unit_suffix = str(data_viz.get("chartUnitSuffix") or style.unit_suffix)
    style.legend_style = _one_of(data_viz.get("chartLegend"), LEGEND_STYLES,
                                 style.legend_style)
    style.unit_convention = _one_of(data_viz.get("chartUnitConvention"),
                                    UNIT_CONVENTIONS, style.unit_convention)
    style.bar_thickness = _positive(data_viz.get("chartBarThickness"),
                                    style.bar_thickness)
    style.bar_group_gap = max(0.0, _number(data_viz.get("chartBarGroupGap"),
                                           style.bar_group_gap))
    style.font_fallbacks = font_chain(design_system, style.font_name)
    return style


def font_chain(design_system: dict | None, font_name: str) -> list[str]:
    """
    The stack to try for a face: the deck's recorded chain, or one derived.

    Extraction records a chain per role. A design system written before that
    existed - or one a model hand-assembled - has only a name, so the chain is
    derived from the name's own family, which is still better than the sans
    stack the chart renderer used to fall back to unconditionally.
    """
    from app.measure.density import fallback_chain

    body = (((design_system or {}).get("typography") or {}).get("bodyFont")) or {}
    declared = body.get("fallbacks") or []
    if declared and str(body.get("typeface") or "").strip() == font_name.strip():
        return [str(face) for face in declared if str(face).strip()]
    return fallback_chain(font_name)


def _flag(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "yes", "on", "1", "visible", "shown"):
        return True
    if text in ("false", "no", "off", "0", "hidden", "none"):
        return False
    return default


def _one_of(value: Any, allowed: frozenset, default: str) -> str:
    text = str(value or "").strip().lower()
    return text if text in allowed else default


def _number(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _positive(value: Any, default: float) -> float:
    number = _number(value, default)
    return number if number > 0 else default
