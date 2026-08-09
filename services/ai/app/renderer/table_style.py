"""
Resolves table and chart styling from a run's design system, so generated
tables/charts pick up the template's own brand colors instead of a generic
default - e.g. a Starbucks-templated deck gets a Starbucks-green table
header and chart series automatically.

Prefers an explicit `dataViz` config (written once during design-system
extraction - see extract_template_design_system / design_system.py's
derive_data_viz_config) and falls back to deriving reasonable colors from
the base `palette` when `dataViz` isn't present, so table/chart rendering
doesn't hard-depend on that extraction step having populated it.
"""

from __future__ import annotations
from dataclasses import dataclass

DEFAULT_HEADER_BG = "#1F4E79"
DEFAULT_HEADER_FG = "#FFFFFF"
DEFAULT_BAND_BG = "#F2F2F2"
DEFAULT_ROW_BG = "#FFFFFF"
DEFAULT_BORDER = "#CCCCCC"
DEFAULT_FONT = "Arial"
DEFAULT_AXIS_COLOR = "#404040"
DEFAULT_GRID_COLOR = "#E0E0E0"
DEFAULT_CHART_COLOR_SEQUENCE = ["#1F4E78", "#2980B9", "#FF7F50", "#808080", "#15334F"]


@dataclass
class TableStyle:
    header_bg: str
    header_fg: str
    band_bg: str
    row_bg: str
    border: str
    font_name: str


@dataclass
class ChartStyle:
    color_sequence: list[str]
    axis_color: str
    grid_color: str
    font_name: str


def resolve_table_style(design_system: dict | None) -> TableStyle:
    design_system = design_system or {}
    data_viz = design_system.get("dataViz") or {}

    if data_viz.get("tableHeaderBg"):
        return TableStyle(
            header_bg=data_viz.get("tableHeaderBg", DEFAULT_HEADER_BG),
            header_fg=data_viz.get("tableHeaderFg", DEFAULT_HEADER_FG),
            band_bg=data_viz.get("tableBandBg", DEFAULT_BAND_BG),
            row_bg=data_viz.get("tableRowBg", DEFAULT_ROW_BG),
            border=data_viz.get("tableBorder", DEFAULT_BORDER),
            font_name=data_viz.get("fontFamily", DEFAULT_FONT),
        )

    palette = ((design_system.get("palette") or {}).get("colors")) or []
    primary = next((c.get("value") for c in palette if c.get("name") == "primary"), None)
    body_font = (((design_system.get("typography") or {}).get("bodyFont")) or {}).get("typeface")

    return TableStyle(
        header_bg=primary or DEFAULT_HEADER_BG,
        header_fg=DEFAULT_HEADER_FG,
        band_bg=DEFAULT_BAND_BG,
        row_bg=DEFAULT_ROW_BG,
        border=DEFAULT_BORDER,
        font_name=body_font or DEFAULT_FONT,
    )


def resolve_chart_style(design_system: dict | None) -> ChartStyle:
    design_system = design_system or {}
    data_viz = design_system.get("dataViz") or {}

    if data_viz.get("chartColorSequence"):
        return ChartStyle(
            color_sequence=list(data_viz["chartColorSequence"]),
            axis_color=data_viz.get("chartAxisColor", DEFAULT_AXIS_COLOR),
            grid_color=data_viz.get("chartGridColor", DEFAULT_GRID_COLOR),
            font_name=data_viz.get("fontFamily", DEFAULT_FONT),
        )

    palette = ((design_system.get("palette") or {}).get("colors")) or []
    sequence = [c.get("value") for c in palette if c.get("value")][:5]
    body_font = (((design_system.get("typography") or {}).get("bodyFont")) or {}).get("typeface")

    return ChartStyle(
        color_sequence=sequence or list(DEFAULT_CHART_COLOR_SEQUENCE),
        axis_color=DEFAULT_AXIS_COLOR,
        grid_color=DEFAULT_GRID_COLOR,
        font_name=body_font or DEFAULT_FONT,
    )
