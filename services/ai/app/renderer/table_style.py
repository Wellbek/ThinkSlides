"""
Resolves table (and, from Stage D onward, chart) styling from a run's
design system, so generated tables/charts pick up the template's own brand
colors instead of a generic default - e.g. a Starbucks-templated deck gets
a Starbucks-green table header automatically.

Prefers an explicit `dataViz` config (written once during design-system
extraction - see extract_template_design_system) and falls back to
deriving reasonable colors from the base `palette` when `dataViz` isn't
present, so table rendering doesn't hard-depend on that extraction step
having populated it.
"""

from __future__ import annotations
from dataclasses import dataclass

DEFAULT_HEADER_BG = "#1F4E79"
DEFAULT_HEADER_FG = "#FFFFFF"
DEFAULT_BAND_BG = "#F2F2F2"
DEFAULT_ROW_BG = "#FFFFFF"
DEFAULT_BORDER = "#CCCCCC"
DEFAULT_FONT = "Arial"


@dataclass
class TableStyle:
    header_bg: str
    header_fg: str
    band_bg: str
    row_bg: str
    border: str
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
