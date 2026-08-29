"""
Renders ChartContent slide data to a PNG via matplotlib, styled from the
run's design system (table_style.resolve_chart_style).

Charts are rendered as images rather than native OOXML <c:chart> parts:
matplotlib gives full control over brand colors/fonts, and the result
renders identically in LibreOffice and PowerPoint since it's just a
picture - native chart parts are comparatively fragile and inconsistently
rendered across chart engines, which matters more here than editability.

Because it is a picture, everything a reader needs to interpret the chart
has to be drawn *into* it. Library defaults render bare: the old version
emitted no value-axis label, no data labels, and suppressed the legend
whenever there was exactly one series - which is the common case. A revenue
chart therefore showed a y-axis reading "24 ... 36" with the string "$B"
appearing nowhere on the slide, because the unit lived in the series name.
"""

from __future__ import annotations
import io
import math
from typing import Sequence

import matplotlib
matplotlib.use("Agg")  # headless - this service has no display server
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.ticker import FuncFormatter

from app.renderer.table_style import (
    DEFAULT_CHART_COLOR_SEQUENCE, resolve_chart_style,
)
from app.observability.logging import get_logger

logger = get_logger(__name__)

SUPPORTED_CHART_TYPES = {
    "bar", "barh", "line", "pie", "scatter", "area", "bubble",
    # The bar family. All three stack or step segments along one axis, and all
    # three exist because a plain bar chart answers a different question:
    # "stacked" says what a total is made of, "stacked100" says how that
    # composition shifted when the total barely moved, and "waterfall" says
    # where the movement between two totals came from.
    "stacked", "stacked100", "waterfall",
    # The comparison family. Each answers a question about the *relationship*
    # between two states rather than about a quantity, which is why none of
    # them is a bar chart with different arithmetic.
    #   slope     - who moved up and who moved down between two dates
    #   dumbbell  - how big the gap between two states is, per category
    #   bullet    - whether a measure is hitting its target
    "slope", "dumbbell", "bullet",
    # And three that stand on their own.
    #   donut   - parts of one whole, with the total readable in the middle
    #   heatmap - where the hot spots are in a dense matrix
    #   combo   - a volume and a rate that only mean something together
    "donut", "heatmap", "combo",
    # And one that is a grid of charts rather than a chart: the same shape
    # repeated per segment, on one shared scale, to say whether a pattern
    # holds everywhere or one segment is carrying it.
    "smallmultiples",
}

# Names a caller may reasonably write, mapped to the canonical one. The
# renderer lowercases whatever it is given, so a camelCase type registered in
# camelCase can never match it - "smallMultiples" resolved to "bar" and drew a
# grouped bar chart, silently, for exactly the same reason "barh" never fired.
# Everything a caller can send has to be reachable from the lowercased form.
CHART_TYPE_ALIASES = {
    "smallmultiples": "smallmultiples",
    "small_multiples": "smallmultiples",
    "smallmultiple": "smallmultiples",
    "grid": "smallmultiples",
    "stacked_100": "stacked100",
    "stacked100pct": "stacked100",
    "horizontalbar": "barh",
    "barhorizontal": "barh",
    "doughnut": "donut",
}


def resolve_chart_type(raw: object) -> tuple[str, bool]:
    """
    The canonical type for what a caller wrote, and whether it was recognised.

    Returning the recognised flag rather than silently defaulting is the point.
    A type that falls back to "bar" still renders a valid PNG, so nothing
    downstream - including a test asserting "a PNG came out" - can tell the
    difference between the chart that was asked for and a bar chart. Both of
    this module's two type bugs hid behind exactly that.
    """
    text = str(raw or "bar").strip().lower()
    canonical = CHART_TYPE_ALIASES.get(text, text)
    return (canonical, True) if canonical in SUPPORTED_CHART_TYPES else ("bar", False)

# Past this many panels nothing in any of them is legible at slide size, and
# the grid has stopped being a comparison and become a contact sheet.
MAX_PANELS = 6

# How much of a zoomed waterfall sits below its lowest level, as a share of the
# range the bars occupy. See _waterfall_frame.
ANCHOR_ROOM = 0.55

# A stack of more than this many segments is a stack nobody can read: the
# middle bands are thinner than their own labels and the legend runs longer
# than the chart.
MAX_STACK_SEGMENTS = 6

# A projected value is drawn as an outline rather than a fill, so a reader can
# see at a glance which part of a bar is measured and which is extrapolated.
# Reference page 110's final bar does exactly this, and the footnote saying so
# is required (see validate_slide_json) - without both, the chart claims a
# measured figure it does not have.
FORECAST_HATCH = "////"
FORECAST_DASH = (0, (4, 3))
# A bubble's area encodes a value, so the radius scales with its square root -
# scaling the radius linearly overstates large values by their square, which
# is the classic way an area chart lies.
BUBBLE_MIN_AREA_PT = 60.0
BUBBLE_MAX_AREA_PT = 2600.0
# Labels are placed clear of the cloud and joined to their bubble by a leader.
LEADER_COLOR_MIX = 0.45
# How much of the plot a dual-encoding legend reserves for itself. Reserved
# inside the axes: an inset hanging off the right edge is outside what
# constrained layout accounts for, and the chart's title gets clipped for it.
LEGEND_WIDTH_FRACTION = 0.30

# Beyond this many points per series, per-point data labels collide into an
# unreadable smear - the chart is better read off the axis.
MAX_LABELLED_POINTS = 8

# ── Furniture against the size the chart is actually drawn at ──────────
#
# A four-exhibit page can be entirely correct and completely unreadable. The
# type already scales with the plot's area, which keeps the *proportion* right,
# and that is exactly the problem: a full page's worth of labels, legend, ticks
# and title, all correctly proportioned into a quarter of a page, is a smear.
#
# So furniture is dropped by size rather than shrunk. Same reasoning as
# MAX_LABELLED_POINTS, applied to the chart's rendered dimensions instead of its
# data count: past a threshold the thing stops carrying information and starts
# occluding it, so it goes.
#
# Areas in square inches. A deck-width exhibit is around 40; a half is 18; a
# quarter of a composed page is 6 or less.
SMALL_CHART_AREA_IN2 = 14.0
TINY_CHART_AREA_IN2 = 7.0

# What a small chart may still label. A quarter-page line with eight labels on
# it is eight overlapping numbers.
SMALL_LABELLED_POINTS = 4
TINY_LABELLED_POINTS = 2

# Ticks at a small size. Six category labels across three inches is six labels
# on top of each other.
SMALL_MAX_TICKS = 5
TINY_MAX_TICKS = 3
# A pie with more slices than this is unreadable; the tail is grouped.
MAX_PIE_SLICES = 7

_FONT_FALLBACKS = ("Arial", "Helvetica", "Liberation Sans", "DejaVu Sans")


def _resolve_font(preferred: str | None,
                  fallbacks: Sequence[str] | None = None) -> str:
    """The first requested font actually installed on this host.

    The design system names a typeface for the deck (often Arial, which no
    Linux container has). matplotlib silently falls back with a warning per
    text object; picking a real family up front keeps the chart's type
    consistent with itself.

    The chain comes from the design system where one was recorded, so a deck
    set in a serif falls back to another serif. Walking the hardcoded sans
    stack instead turned every serif template's charts into sans ones - a
    substitution that is obvious on the page.
    """
    available = {f.name for f in font_manager.fontManager.ttflist}
    chain = list(fallbacks) if fallbacks else list(_FONT_FALLBACKS)
    for candidate in (preferred, *chain):
        if candidate and candidate in available:
            return candidate
    return "DejaVu Sans"


def _format_number(value: float) -> str:
    """A compact, human-readable form of a data value."""
    if value != value or value in (float("inf"), float("-inf")):  # NaN/inf
        return ""
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    if abs(value) >= 100 or float(value).is_integer():
        return f"{value:,.0f}"
    if abs(value) >= 10:
        return f"{value:,.1f}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def _axis_formatter(suffix: str):
    return FuncFormatter(lambda v, _pos: f"{_format_number(v)}{suffix}")


def _value_axis_label(chart_data: dict, series: list[dict]) -> str:
    """
    What the value axis is measuring.

    Explicit ``yAxisTitle`` wins. Otherwise a single series' own name is the
    label - that is where the unit lives ("Revenue ($B)"), and with one
    series a legend repeating it would be noise.
    """
    explicit = (chart_data.get("yAxisTitle") or chart_data.get("valueAxisTitle") or "").strip()
    if explicit:
        return explicit
    if len(series) == 1:
        return str(series[0].get("name") or "").strip()
    return ""


def _clean_series(raw_series) -> list[dict]:
    """Well-formed series only, with values coerced to floats."""
    cleaned = []
    for entry in raw_series or []:
        if not isinstance(entry, dict):
            continue
        values = []
        for value in entry.get("values") or []:
            try:
                values.append(float(value))
            except (TypeError, ValueError):
                values.append(float("nan"))
        if values and not all(v != v for v in values):
            # The forecast fields ride along: rebuilding the series as
            # name+values alone silently dropped them, so a chart declaring a
            # projection rendered as if every figure were measured - which is
            # the exact claim the marking exists to prevent.
            series_entry = {"name": entry.get("name"), "values": values}
            for key in ("forecastFrom", "measured"):
                if entry.get(key) is not None:
                    series_entry[key] = entry[key]
            cleaned.append(series_entry)
    return cleaned


def _group_pie_tail(labels: list[str], values: list[float]) -> tuple[list[str], list[float]]:
    """Collapse everything past the largest MAX_PIE_SLICES into "Other"."""
    if len(values) <= MAX_PIE_SLICES:
        return labels, values
    order = sorted(range(len(values)), key=lambda i: values[i], reverse=True)
    keep = order[:MAX_PIE_SLICES - 1]
    rest = order[MAX_PIE_SLICES - 1:]
    kept_labels = [labels[i] for i in keep] + ["Other"]
    kept_values = [values[i] for i in keep] + [sum(values[i] for i in rest)]
    return kept_labels, kept_values


def _superscript(text: str) -> str:
    """Footnote markers as Unicode superscripts, for text drawn into a raster."""
    from app.renderer.apparatus import to_superscript

    return to_superscript(text)


def _adapt_to_ground(style, ground: str | None):
    """
    Re-colour a chart's furniture for the ground it will be drawn on.

    The PNG is transparent, so the slide (or the region) shows through, and
    the axis, ticks, labels and gridlines are all drawn in the design
    system's ``dark`` tone. On a dark ground that is a chart with invisible
    axes - the same defect as dark text on a dark slide, in the one element
    that had no way to find out what was behind it.
    """
    from dataclasses import replace

    from app import colors

    if not ground or not colors.parse_hex(ground) or not colors.is_dark(ground):
        return style
    return replace(
        style,
        axis_color=colors.readable_on(ground, "#FFFFFF"),
        # A gridline is meant to be barely there; on a dark ground that is a
        # slight lift off the ground rather than a light grey line.
        grid_color=colors.mix(ground, "#FFFFFF", 0.28),
    )


def render_chart_to_png(
    chart_data: dict,
    design_system: dict | None,
    width_in: float,
    height_in: float,
    dpi: int = 200,
    slide_title: str | None = None,
    ground: str | None = None,
) -> bytes | None:
    """
    Render a ChartContent-shaped dict to a transparent-background PNG.

    Returns None (never raises) for malformed/empty chart data or a
    matplotlib failure, so a bad chart degrades to "no chart on this slide"
    rather than aborting the whole render.

    ``slide_title`` suppresses a chart title that merely restates the slide's
    heading, which is the usual case and reads as a stutter. ``ground`` is
    what the transparent PNG will sit on, so the chart's own furniture can be
    coloured to be visible against it.
    """
    if width_in <= 0 or height_in <= 0:
        return None

    chart_type, recognised = resolve_chart_type(
        chart_data.get("chartType") or chart_data.get("chart_type")
    )
    if not recognised:
        # Loud, because the fallback renders a perfectly good chart of the
        # wrong kind and nothing downstream can see that it happened.
        logger.warning(
            "chart_type_not_recognised",
            requested=chart_data.get("chartType") or chart_data.get("chart_type"),
            rendered_as=chart_type,
        )

    # A bubble chart states its data as points carrying two encoded values,
    # not as series over shared categories - so it is the one type with no
    # "series" to check for.
    series = _clean_series(chart_data.get("series") or chart_data.get("data"))
    if not series and chart_type != "bubble":
        return None
    if chart_type == "bubble" and not _bubble_points(chart_data):
        # Bubbles state their data as points carrying two encoded values. Given
        # a plain series instead, the honest rendering is the scatter that data
        # supports rather than nothing at all.
        if not series:
            return None
        chart_type = "scatter"

    # Footnote markers are written inline (see renderer/apparatus). A chart
    # label is drawn into a PNG, where no run-level superscript exists, so the
    # marker becomes a Unicode superscript digit instead of being left as
    # literal "[^1]" in the middle of an axis.
    categories = [_superscript(str(c)) for c in (chart_data.get("categories") or [])]
    for entry in series:
        entry["name"] = _superscript(str(entry.get("name") or ""))

    style = _adapt_to_ground(resolve_chart_style(design_system), ground)
    font = _resolve_font(style.font_name, style.font_fallbacks)

    unit_suffix = str(chart_data.get("valueSuffix") or "").strip()
    if not unit_suffix and str(chart_data.get("valueFormat") or "").lower() == "percent":
        unit_suffix = "%"

    # A chart may override the deck's own data-viz tokens. The deck sets the
    # convention; one exhibit is allowed to differ from it, which is what the
    # reference itself does - most of its charts drop the axis, and the bubble
    # chart on page 3 keeps both.
    style = _apply_chart_overrides(style, chart_data)

    # Point sizes are chosen against the band the chart is drawn into, so a
    # half-height chart does not render with type twice as large relative to
    # its own axes as a full-height one.
    base_pt = max(8.0, min(13.0, 3.6 * math.sqrt(width_in * height_in / 6.0) + 6.0))

    # And the furniture is thinned against the same size. Correctly proportioned
    # is not the same as legible: a full page's worth of labels, legend, ticks
    # and title scaled into a quarter page is a smear, so past a threshold they
    # are dropped rather than shrunk.
    style = _thin_furniture(style, width_in, height_in, chart_data)
    # The two caps that are read deep inside the drawing code travel on the
    # chart dict rather than through eight call signatures. Underscored because
    # they are the renderer talking to itself about this one render - nothing
    # authors them and nothing outside this module reads them.
    chart_data = {
        **chart_data,
        "_maxLabelledPoints": max_labelled_points(width_in, height_in),
        "_maxTicks": max_ticks(width_in, height_in),
    }
    if _is_small(width_in, height_in) and not chart_data.get("keepTitle"):
        # A title inside a small exhibit is a line of type competing with the
        # data for the room. On a composed page the region's own label, or the
        # slide's, is already saying what this is.
        chart_data = {**chart_data, "title": ""}

    if chart_type == "smallmultiples":
        return _render_small_multiples(
            chart_data, series, categories, style, font, unit_suffix,
            width_in, height_in, dpi, slide_title,
        )

    fig = None
    try:
        fig, ax = plt.subplots(figsize=(width_in, height_in), dpi=dpi)
        fig.patch.set_alpha(0.0)
        ax.set_facecolor("none")

        finish_legend = None
        if chart_type in ("pie", "donut"):
            _draw_pie(ax, series, categories, style, font, base_pt,
                      centre_total=chart_type == "donut",
                      unit_suffix=unit_suffix)
        elif chart_type == "bubble":
            finish_legend = _draw_bubble(ax, chart_data, style, font, base_pt)
        elif chart_type == "waterfall":
            _draw_waterfall(ax, series, categories, chart_data, style, font,
                            base_pt, unit_suffix)
        elif chart_type in ("stacked", "stacked100"):
            _draw_stacked(ax, chart_type, series, categories, chart_data, style,
                          font, base_pt, unit_suffix)
        elif chart_type == "slope":
            _draw_slope(ax, series, categories, chart_data, style, font,
                        base_pt, unit_suffix)
        elif chart_type == "dumbbell":
            _draw_dumbbell(ax, series, categories, chart_data, style, font,
                           base_pt, unit_suffix)
        elif chart_type == "bullet":
            _draw_bullet(ax, series, categories, chart_data, style, font,
                         base_pt, unit_suffix)
        elif chart_type == "heatmap":
            _draw_heatmap(ax, series, categories, chart_data, style, font,
                          base_pt, unit_suffix)
        elif chart_type == "combo":
            _draw_combo(ax, series, categories, chart_data, style, font,
                        base_pt, unit_suffix)
        else:
            _draw_cartesian(
                ax, chart_type, series, categories, chart_data,
                style, font, base_pt, unit_suffix,
            )

        _apply_title(fig, ax, chart_data, slide_title, style, font, base_pt)

        # No bbox_inches="tight": it crops the canvas to the drawn extent, so
        # the PNG's aspect ratio stopped matching the figsize it was asked
        # for - and both placement paths then forced the image back into the
        # requested rectangle, stretching every chart non-uniformly. Reserving
        # the margins instead keeps the saved pixels exactly figsize * dpi.
        fig.set_layout_engine("constrained")

        if finish_legend is not None:
            # Drawn after the layout has settled, because a size key has to be
            # made of *circles*: the correction from data units to equal
            # display units depends on the axes' final proportions, and before
            # the layout runs those are the defaults rather than the real ones.
            # An elliptical size key is a legend that misstates the encoding it
            # exists to explain.
            fig.canvas.draw()
            finish_legend()

        buf = io.BytesIO()
        fig.savefig(buf, format="png", transparent=True)
        return buf.getvalue()
    except Exception as e:
        logger.warning("chart_render_failed", chart_type=chart_type, error=str(e))
        return None
    finally:
        if fig is not None:
            plt.close(fig)


def _apply_chart_overrides(style, chart_data: dict):
    """One chart's own departures from the deck's data-viz tokens."""
    from dataclasses import replace

    from app.renderer.table_style import LABEL_MODES, LEGEND_STYLES

    overrides = {}
    mode = str(chart_data.get("labelMode") or "").strip().lower()
    if mode in LABEL_MODES:
        overrides["label_mode"] = mode
    legend = str(chart_data.get("legendStyle") or "").strip().lower()
    if legend in LEGEND_STYLES:
        overrides["legend_style"] = legend
    for key, field in (("showValueAxis", "show_value_axis"),
                       ("showGridlines", "show_value_gridlines")):
        if isinstance(chart_data.get(key), bool):
            overrides[field] = chart_data[key]
    return replace(style, **overrides) if overrides else style


def _apply_title(fig, ax, chart_data, slide_title, style, font, base_pt) -> None:
    """
    Draw the chart's own title, and the unit line under it.

    The reference states its unit as a second line of the subtitle -
    "thousands", "$ billion" - set apart from the title rather than rotated up
    the side of the plot. That is a deck convention (see the unit tokens in
    table_style), and this is where it lands.
    """
    title = _superscript(str(chart_data.get("title") or "").strip())
    unit_line = _superscript(str(chart_data.get("unitLine") or "").strip())
    if not unit_line and style.unit_convention == "subtitle":
        unit_line = _superscript(_value_axis_label(chart_data,
                                                   _clean_series(chart_data.get("series"))))

    if title and slide_title and _normalise(title) == _normalise(slide_title):
        title = ""
    if not title and not unit_line:
        return

    if title and unit_line:
        # Two lines, the second smaller and quieter: it is a unit, not a
        # second heading.
        ax.set_title(title, color=style.axis_color, fontname=font,
                     fontsize=base_pt * 1.15, fontweight="bold", loc="left",
                     pad=base_pt * 1.9)
        ax.annotate(
            unit_line, xy=(0.0, 1.0), xycoords="axes fraction",
            xytext=(0, base_pt * 0.5), textcoords="offset points",
            ha="left", va="bottom", color=style.axis_color, fontname=font,
            fontsize=base_pt * 0.95,
        )
        return

    ax.set_title(
        title or unit_line, color=style.axis_color, fontname=font,
        fontsize=base_pt * 1.15, fontweight="bold" if title else "normal",
        loc="left", pad=base_pt,
    )


def _normalise(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _set_grid(axis, visible: bool, style) -> None:
    """Turn one axis's gridlines on (styled) or off (silently)."""
    if visible:
        axis.grid(True, color=style.grid_color, linewidth=style.gridline_weight_pt)
    else:
        axis.grid(False)


def _forecast_from(series_entry: dict, n_points: int) -> int:
    """The index at which a series stops being measured, or n_points."""
    raw = series_entry.get("forecastFrom")
    try:
        index = int(raw)
    except (TypeError, ValueError):
        return n_points
    return max(0, min(n_points, index))


def _measured_values(series_entry: dict, values: list, forecast_from: int) -> list:
    """
    The measured portion of each value.

    Two shapes, and both are needed. A series that simply stops being measured
    at some index projects everything after it, so those points have no
    measured portion at all. Page 110's final bar is the other shape: 18.3
    measured and 36.6 extrapolated, so the bar is solid to the measured figure
    and outlined to the projected one, and a reader can see which part of it
    is evidence.
    """
    raw = series_entry.get("measured")
    stated = isinstance(raw, (list, tuple))
    out = []
    for index, value in enumerate(values):
        if stated:
            try:
                measured = float(raw[index])
            except (TypeError, ValueError, IndexError):
                measured = value if index < forecast_from else 0.0
        else:
            measured = value if index < forecast_from else 0.0
        out.append(measured if measured == measured else 0.0)
    return out


def _solid_part(value: float, measured: float) -> float:
    """How much of one bar is measured rather than projected, sign kept.

    The measured portion lies between zero and the value itself, on the
    value's own side of the baseline. Stating it that way rather than as
    `max(0.0, min(value, measured))` is the whole fix for issue #292: that
    clamp read every negative datum as arithmetic that had gone wrong and
    flattened it to a zero-height bar, so a chart of declines drew nothing
    at all - silently, because a chart with no bars still has a title and
    axes and still attaches to its slide.

    The guard the clamp was written for is kept: a stated `measured` larger
    than the value cannot push the solid part past the value, in either
    direction.
    """
    if value != value or measured != measured:   # NaN
        return 0.0
    low, high = (value, 0.0) if value < 0 else (0.0, value)
    return min(max(measured, low), high)


def _draw_bars_with_forecast(ax, offsets, values, measured, forecast_from,
                             bar_width, color, name, horizontal=False) -> None:
    """
    Bars, with any projected part drawn as an outline rather than a fill.

    A projection filled the same as a measurement is a chart asserting a
    figure it does not have. The outline is the visual claim "this part is
    extrapolated", and #125's footnote is the words for it - validation
    requires both.
    """
    solid = [_solid_part(value, measured[index])
             for index, value in enumerate(values)]
    if horizontal:
        ax.barh(offsets, solid, height=bar_width, label=name, color=color)
    else:
        ax.bar(offsets, solid, width=bar_width, label=name, color=color)

    for index, value in enumerate(values):
        base = solid[index]
        # Anything of the bar the solid part did not cover, in whichever
        # direction the value runs. `value - base > 0` was the test here,
        # which skipped the outline for every negative bar as surely as the
        # old clamp skipped its fill - see issue #292.
        if value != value or value - base == 0:
            continue
        outline = dict(facecolor="none", edgecolor=color, linewidth=1.4,
                       linestyle=FORECAST_DASH, hatch=None)
        if horizontal:
            ax.barh([offsets[index]], [value - base], left=[base],
                    height=bar_width, **outline)
        else:
            ax.bar([offsets[index]], [value - base], bottom=[base],
                   width=bar_width, **outline)


def _draw_line_with_forecast(ax, x, values, forecast_from, color, name,
                             base_pt) -> None:
    """A line whose projected tail is dashed, joined to the measured part."""
    if forecast_from >= len(values):
        ax.plot(x, values, label=name, color=color, linewidth=2.5, marker="o",
                markersize=base_pt * 0.5)
        return

    split = max(0, forecast_from - 1)
    ax.plot(x[:forecast_from], values[:forecast_from], label=name, color=color,
            linewidth=2.5, marker="o", markersize=base_pt * 0.5)
    ax.plot(x[split:], values[split:], color=color, linewidth=2.0,
            linestyle=FORECAST_DASH, marker="o", markersize=base_pt * 0.5,
            markerfacecolor="none")


def _bar_geometry(style, n_series: int, index: int, x: list) -> tuple[float, list]:
    """
    Bar width and per-series offsets, from the deck's own bar thickness.

    A template whose bars are thin and widely spaced and one whose bars nearly
    touch are visibly different charts of the same data, and the difference is
    two numbers.
    """
    n_series = max(1, n_series)
    span = max(0.05, min(1.0, style.bar_thickness))
    gap = max(0.0, min(0.5, style.bar_group_gap))
    bar_width = span / n_series * (1.0 - gap)
    start = -span / 2 + bar_width / 2 + index * (span / n_series)
    return bar_width, [xi + start for xi in x]


def _tick_stride(count: int, cap: object) -> int:
    """Label every nth category, so no more than ``cap`` labels are drawn."""
    limit = int(cap) if isinstance(cap, int) and cap > 0 else 0
    if not limit or count <= limit:
        return 1
    return -(-count // limit)


def _is_small(width_in: float, height_in: float) -> bool:
    return width_in * height_in <= SMALL_CHART_AREA_IN2


def _is_tiny(width_in: float, height_in: float) -> bool:
    return width_in * height_in <= TINY_CHART_AREA_IN2


def max_labelled_points(width_in: float, height_in: float) -> int:
    """How many points this chart may label, at the size it is drawn."""
    if _is_tiny(width_in, height_in):
        return TINY_LABELLED_POINTS
    if _is_small(width_in, height_in):
        return SMALL_LABELLED_POINTS
    return MAX_LABELLED_POINTS


def max_ticks(width_in: float, height_in: float) -> int | None:
    """How many category ticks fit, or None for "as many as there are"."""
    if _is_tiny(width_in, height_in):
        return TINY_MAX_TICKS
    if _is_small(width_in, height_in):
        return SMALL_MAX_TICKS
    return None


def _thin_furniture(style, width_in: float, height_in: float,
                    chart_data: dict):
    """The chart style, with what will not fit taken out of it.

    Dropped, not scaled. A legend at 5pt beside a two-inch plot is a legend
    nobody reads that has taken a third of the plot to say so, and gridlines
    under a three-inch chart are a grey wash. What survives at a small size is
    the data, its axis, and the one or two labels that carry the finding.
    """
    if not _is_small(width_in, height_in):
        return style

    updates = {"show_value_gridlines": False}
    if _is_tiny(width_in, height_in):
        # A legend on a tiny chart is worse than no legend: the plot it leaves
        # behind is too small to compare the series it is naming.
        updates["legend_style"] = "none"
        updates["show_value_axis"] = False
    from dataclasses import replace as _replace

    # An explicit override on the chart itself still wins - the deck sets the
    # convention, one exhibit may differ, and a caller who said "keep the
    # legend" on a small chart meant it.
    stated = {
        "legend_style" if "legendStyle" in chart_data else "": None,
        "show_value_axis" if "showValueAxis" in chart_data else "": None,
    }
    for key in list(updates):
        if key in stated:
            updates.pop(key)
    return _replace(style, **updates)


def _legend_wanted(style, n_series: int) -> bool:
    """Whether this chart draws a legend at all."""
    if style.legend_style == "none":
        return False
    if style.legend_style in ("swatches", "inline"):
        return n_series > 1
    return n_series > 1


def _points_to_label(style, chart_type: str, x: list) -> set:
    """
    Which points carry a data label.

    ``terminal`` is reference page 110's line chart: one label, on the final
    point, saying where the series ended up. ``end`` is page 22's bar chart:
    a label at the end of every bar, which is what lets the chart drop its
    axis entirely.
    """
    if not x:
        return set()
    if style.label_mode == "terminal":
        return {len(x) - 1}
    return set(range(len(x)))


def _draw_pie(ax, series, categories, style, font, base_pt,
              centre_total: bool = False, unit_suffix: str = "") -> None:
    """A donut with a side legend carrying each slice's share.

    A bare pie in a wide content band left the left and right thirds of the
    slide empty and named its slices without ever saying what share they
    were - the one number a reader wants from a pie.

    ``centre_total`` is the whole difference between "pie" and "donut" here,
    and it is not decoration: the hole already exists, and a mix chart whose
    headline total is readable in the middle answers "of what?" without
    spending a second exhibit on it. A pie leaves the hole empty because its
    total is often not a number anyone quotes (three shares of "responses"),
    and printing one there would invite reading it as a figure.
    """
    values = [v for v in series[0]["values"] if v == v and v >= 0]
    labels = categories[:len(values)] or [f"Slice {i + 1}" for i in range(len(values))]
    labels = [str(name) for name in labels]
    if len(labels) < len(values):
        labels += [f"Slice {i + 1}" for i in range(len(labels), len(values))]
    labels, values = _group_pie_tail(labels, values)

    total = sum(values)
    if total <= 0:
        raise ValueError("pie chart values sum to zero")

    colors = [style.color_sequence[i % len(style.color_sequence)] for i in range(len(values))]
    wedges, _ = ax.pie(
        values,
        colors=colors,
        startangle=90,
        counterclock=False,
        wedgeprops={"width": 0.42, "edgecolor": "none"},
    )
    ax.axis("equal")

    legend = ax.legend(
        wedges,
        [f"{label} - {value / total * 100:.0f}%" for label, value in zip(labels, values)],
        loc="center left",
        # Just clear of the donut, not hard against the band's right edge -
        # the two read as one object rather than a chart and a stray list.
        bbox_to_anchor=(0.88, 0.5),
        frameon=False,
        fontsize=base_pt,
        labelcolor=style.axis_color,
        handlelength=1.1,
        borderpad=0.0,
    )
    for text in legend.get_texts():
        text.set_fontname(font)

    if centre_total and not _total_is_meaningless(total, unit_suffix):
        ax.annotate(
            f"{_format_number(total)}{unit_suffix}",
            xy=(0, 0), xycoords="data", ha="center", va="center",
            color=style.axis_color, fontname=font,
            fontsize=base_pt * 1.6, fontweight="bold",
        )


def _total_is_meaningless(total: float, unit_suffix: str) -> bool:
    """Whether a donut's centre total says anything worth the space.

    A donut of absolute values earns its centre - "$4.15B" is the thing the
    slices are shares of. A donut of percentages does not: the total is 100 by
    construction, and a bare "100" in the middle of a ring reads as a figure
    when it is an artefact of the arithmetic.
    """
    if "%" in (unit_suffix or ""):
        return True
    return abs(total - 100.0) < 0.51


def _bubble_points(chart_data: dict) -> list[dict]:
    """Well-formed bubble points only, with their two encoded values."""
    out = []
    for entry in chart_data.get("points") or []:
        if not isinstance(entry, dict):
            continue
        try:
            x = float(entry.get("x"))
            y = float(entry.get("y"))
        except (TypeError, ValueError):
            continue
        try:
            size = float(entry.get("size", 1.0))
        except (TypeError, ValueError):
            size = 1.0
        out.append({
            "label": str(entry.get("label") or "").strip(),
            "x": x, "y": y, "size": max(0.0, size),
            "color_value": entry.get("colorValue"),
        })
    return out


def _draw_bubble(ax, chart_data, style, font, base_pt):
    """
    A bubble chart carrying **two** encodings at once - colour and area - with
    labels placed clear of the cloud and a legend explaining both.

    Reference page 3 is this chart: colour for adoption score, area for
    investment, leader lines out to labels, and a nested-arc size key. Every
    part of it matters. A bubble chart with one encoding is a scatter plot; a
    bubble chart with two and no legend is a picture of some circles.

    Area, not radius, encodes the value - matplotlib's ``s`` is an area in
    points squared, so scaling it linearly in the value is correct by
    construction. Scaling the radius instead would overstate large values by
    their square, which is the classic way an area chart lies.
    """
    points = _bubble_points(chart_data)
    if not points:
        raise ValueError("bubble chart has no usable points")

    sizes = [p["size"] for p in points]
    largest = max(sizes) or 1.0
    areas = [
        BUBBLE_MIN_AREA_PT + (BUBBLE_MAX_AREA_PT - BUBBLE_MIN_AREA_PT)
        * (size / largest)
        for size in sizes
    ]
    colours = [
        _bubble_colour(point["color_value"], style, index)
        for index, point in enumerate(points)
    ]

    has_legend = bool(chart_data.get("colorLegend") or chart_data.get("sizeLegend"))
    # Limits are set before anything is drawn so the label placement below can
    # convert a bubble's radius from points into data units. Room for the
    # legend is reserved inside the axes rather than beside them: an inset
    # hanging off the right edge is outside what constrained layout accounts
    # for, and the chart's own title gets clipped to make room for it.
    _set_bubble_limits(ax, points, areas, reserve_right=LEGEND_WIDTH_FRACTION
                       if has_legend else 0.0)

    ax.scatter([p["x"] for p in points], [p["y"] for p in points],
               s=areas, c=colours, edgecolors="none", zorder=3)

    _label_bubbles(ax, points, areas, style, font, base_pt)

    for axis_key, setter in (("xAxisTitle", ax.set_xlabel),
                             ("yAxisTitle", ax.set_ylabel)):
        label = str(chart_data.get(axis_key) or "").strip()
        if label:
            setter(label, color=style.axis_color, fontname=font,
                   fontsize=base_pt * 0.9, labelpad=base_pt * 0.5)

    ax.tick_params(colors=style.axis_color, labelsize=base_pt * 0.85)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(style.axis_color)
    _set_grid(ax.yaxis, style.show_value_gridlines, style)
    _set_grid(ax.xaxis, style.show_category_gridlines, style)
    ax.set_axisbelow(True)

    # The space reserved for the legend is not part of the data range, so it
    # gets no tick marks - an axis running on under a legend reads as data
    # that is not there.
    if has_legend:
        furthest = max(p["x"] for p in points)
        ax.set_xticks([t for t in ax.get_xticks() if t <= furthest * 1.05])

    if not has_legend:
        return None
    return lambda: _draw_bubble_legend(ax, chart_data, style, font, base_pt)


def _set_bubble_limits(ax, points, areas, reserve_right: float) -> None:
    """Axis limits with room for the bubbles themselves and for the legend."""
    xs = [p["x"] for p in points]
    ys = [p["y"] for p in points]
    x_span = (max(xs) - min(xs)) or 1.0
    y_span = (max(ys) - min(ys)) or 1.0
    pad_x, pad_y = x_span * 0.18, y_span * 0.22

    left, right = min(xs) - pad_x, max(xs) + pad_x
    width = right - left
    if reserve_right > 0:
        right = left + width / (1.0 - reserve_right)
    ax.set_xlim(left, right)
    ax.set_ylim(min(ys) - pad_y, max(ys) + pad_y)


def _data_radius(ax, area_pt: float) -> tuple[float, float]:
    """A bubble's radius in data units, on each axis."""
    figure = ax.get_figure()
    radius_pt = math.sqrt(max(area_pt, 1.0) / math.pi)
    width_pt = ax.get_window_extent().width / figure.dpi * 72.0
    height_pt = ax.get_window_extent().height / figure.dpi * 72.0
    x_lo, x_hi = ax.get_xlim()
    y_lo, y_hi = ax.get_ylim()
    return (
        radius_pt / max(1.0, width_pt) * (x_hi - x_lo),
        radius_pt / max(1.0, height_pt) * (y_hi - y_lo),
    )


def _bubble_colour(color_value, style, index: int) -> str:
    """
    The colour encoding a bubble's second dimension.

    A declared ``colorValue`` indexes the deck's own series colours, so the
    encoding stays in the deck's palette rather than importing a colormap.
    """
    sequence = style.color_sequence or list(DEFAULT_CHART_COLOR_SEQUENCE)
    try:
        return sequence[int(color_value) % len(sequence)]
    except (TypeError, ValueError):
        return sequence[index % len(sequence)]


def _label_bubbles(ax, points, areas, style, font, base_pt) -> None:
    """
    Label each bubble, placed clear of the cloud and joined by a leader line.

    A label inside a small bubble is unreadable and a label on top of its
    neighbour is worse, so each label is pushed out past its own bubble's
    radius, away from the cloud's centre, and then separated vertically from
    the others on its side. A hairline connects it back to what it names -
    which is the whole point of a leader line, and why the label may be moved
    at all.
    """
    from app import colors as color_utils

    centre_x = sum(p["x"] for p in points) / len(points)
    centre_y = sum(p["y"] for p in points) / len(points)
    leader = color_utils.mix(style.axis_color, style.grid_color, LEADER_COLOR_MIX)

    x_lo, x_hi = ax.get_xlim()
    y_lo, y_hi = ax.get_ylim()
    x_span, y_span = x_hi - x_lo, y_hi - y_lo
    line_height = y_span * base_pt / 260.0

    placed: list[dict] = []
    for point, area in zip(points, areas):
        if not point["label"]:
            continue
        radius_x, radius_y = _data_radius(ax, area)
        dx = (point["x"] - centre_x) / (x_span or 1.0)
        dy = (point["y"] - centre_y) / (y_span or 1.0)
        magnitude = math.hypot(dx, dy) or 1.0
        gap_x = radius_x + x_span * 0.03
        gap_y = radius_y + y_span * 0.03
        to_the_right = dx >= 0
        label_x = point["x"] + dx / magnitude * gap_x * 1.6
        # A label pushed off the left edge collides with the axis furniture,
        # so it goes to the right of its bubble instead - the leader line is
        # what makes either side legible.
        if not to_the_right and label_x < x_lo + x_span * 0.16:
            to_the_right = True
            label_x = point["x"] + gap_x * 1.6
        placed.append({
            "point": point,
            "x": label_x,
            "y": point["y"] + dy / magnitude * gap_y * 1.6,
            "right": to_the_right,
        })

    # Separate labels that would sit on top of each other, one side at a time.
    for side in (True, False):
        column = sorted((p for p in placed if p["right"] is side),
                        key=lambda entry: entry["y"])
        for index in range(1, len(column)):
            gap = column[index]["y"] - column[index - 1]["y"]
            if gap < line_height:
                column[index]["y"] = column[index - 1]["y"] + line_height

    for entry in placed:
        ax.annotate(
            entry["point"]["label"],
            xy=(entry["point"]["x"], entry["point"]["y"]),
            xytext=(
                min(max(entry["x"], x_lo + x_span * 0.01), x_hi - x_span * 0.01),
                min(max(entry["y"], y_lo + y_span * 0.02), y_hi - y_span * 0.02),
            ),
            ha="left" if entry["right"] else "right",
            va="center",
            color=style.axis_color, fontname=font, fontsize=base_pt * 0.78,
            arrowprops={"arrowstyle": "-", "linewidth": 0.6, "color": leader,
                        "shrinkA": 0, "shrinkB": 1},
            zorder=4,
        )


def _draw_bubble_legend(ax, chart_data, style, font, base_pt) -> None:
    """
    The hand-built legend that explains both encodings.

    Colour as a swatch row, size as **nested arcs sharing a baseline** - the
    key the reference uses, and the only one that shows a reader what an area
    of that size actually looks like. A dual-encoded chart without this is
    asking the reader to guess which dimension is which.

    A circle in an axes whose x and y scales differ is an ellipse, which is
    exactly what a *size* key must not be - so every circle here is drawn as
    an ellipse with the axes' own aspect divided back out. That correction
    needs the final layout, which is why this runs after it.
    """
    from matplotlib.patches import Ellipse

    colour_legend = chart_data.get("colorLegend") or {}
    size_legend = chart_data.get("sizeLegend") or {}

    panel = ax.inset_axes([1.0 - LEGEND_WIDTH_FRACTION + 0.02, 0.0,
                           LEGEND_WIDTH_FRACTION - 0.03, 1.0])
    panel.set_xlim(0, 1)
    panel.set_ylim(0, 1)
    panel.axis("off")

    extent = panel.get_window_extent()
    # One data unit is this many display units on each axis; dividing it out
    # of the width is what makes a "circle" round.
    aspect = (extent.height / extent.width) if extent.width else 1.0

    def circle(centre, radius, **kwargs):
        panel.add_patch(Ellipse(centre, 2 * radius * aspect, 2 * radius, **kwargs))

    cursor = 0.97
    swatch_r = 0.028
    labels = [str(v) for v in (colour_legend.get("labels") or [])]
    if labels:
        panel.text(0.0, cursor, str(colour_legend.get("title") or "").strip(),
                   color=style.axis_color, fontname=font,
                   fontsize=base_pt * 0.8, fontweight="bold", va="top")
        cursor -= 0.07
        step = min(0.16, 0.95 / max(1, len(labels)))
        for index, label in enumerate(labels):
            x = index * step
            circle((x + swatch_r * aspect, cursor), swatch_r,
                   facecolor=_bubble_colour(index, style, index), edgecolor="none")
            panel.text(x + swatch_r * aspect * 2.4, cursor, label,
                       color=style.axis_color, fontname=font,
                       fontsize=base_pt * 0.7, va="center")
        cursor -= 0.11

    values = [float(v) for v in (size_legend.get("values") or []) if v is not None]
    if not values:
        return
    panel.text(0.0, cursor, str(size_legend.get("title") or "").strip(),
               color=style.axis_color, fontname=font, fontsize=base_pt * 0.8,
               fontweight="bold", va="top")
    cursor -= 0.06

    biggest = max(values) or 1.0
    max_radius = 0.13
    baseline = cursor - max_radius * 2
    for value in sorted(values, reverse=True):
        radius = max_radius * math.sqrt(value / biggest)
        circle((max_radius * aspect, baseline + radius), radius,
               facecolor="none", edgecolor=style.axis_color, linewidth=0.8)
        panel.text(max_radius * aspect * 2.3, baseline + radius * 2,
                   _format_number(value), color=style.axis_color, fontname=font,
                   fontsize=base_pt * 0.68, va="center")


def _draw_cartesian(
    ax, chart_type, series, categories, chart_data, style, font, base_pt, unit_suffix,
) -> None:
    # A horizontal bar chart is the same chart with its axes exchanged. That
    # exchange lives in _cartesian_furniture, which every chart's axes go
    # through - keeping it in one place is why the one line that already
    # mentioned "barh" could be wrong for so long without anyone noticing.
    horizontal = chart_type == "barh"

    n_points = len(categories) if categories else max(len(s["values"]) for s in series)
    x = list(range(n_points))

    for i, s in enumerate(series):
        values = (s["values"] + [float("nan")] * n_points)[:n_points]
        name = str(s.get("name") or f"Series {i + 1}")
        color = style.color_sequence[i % len(style.color_sequence)]

        # Which points are projected rather than measured. Everything from
        # forecastFrom onwards, plus any point whose measured portion is
        # smaller than its value (page 110's split final bar).
        forecast_from = _forecast_from(s, n_points)
        measured = _measured_values(s, values, forecast_from)

        if chart_type == "line":
            _draw_line_with_forecast(ax, x, values, forecast_from, color, name,
                                     base_pt)
        elif chart_type == "area":
            ax.plot(x, values, label=name, color=color, linewidth=2.0)
            ax.fill_between(x, values, color=color, alpha=0.22)
        elif chart_type == "scatter":
            ax.scatter(x, values, label=name, color=color, s=base_pt * 6)
        else:  # bar / barh
            bar_width, offsets = _bar_geometry(style, len(series), i, x)
            _draw_bars_with_forecast(ax, offsets, values, measured, forecast_from,
                                     bar_width, color, name, horizontal)

    _cartesian_furniture(
        ax, categories, chart_data, style, font, base_pt, unit_suffix, x,
        # A bar's length *is* its value, so a non-zero baseline misstates the
        # comparison. Line and scatter show change, where a zoomed axis is
        # legitimate, so they keep matplotlib's own range.
        zero_baseline=chart_type in ("bar", "barh", "area"),
        horizontal=horizontal,
    )

    # Where the unit is stated is a deck convention, not a chart decision.
    # The reference puts it on a second subtitle line ("thousands", "$ billion")
    # rather than rotated up the side of the plot.
    # An explicit unitLine says the unit outright, so repeating it up the side
    # of the plot states it twice.
    value_label = _value_axis_label(chart_data, series)
    states_unit_above = bool(str(chart_data.get("unitLine") or "").strip())
    if (value_label and not states_unit_above
            and style.unit_convention == "axis" and style.show_value_axis):
        set_value_label = ax.set_xlabel if horizontal else ax.set_ylabel
        set_value_label(value_label, color=style.axis_color, fontname=font,
                        fontsize=base_pt, labelpad=base_pt * 0.6)


    # A legend for one series would only repeat the value-axis label.
    if _legend_wanted(style, len(series)):
        legend = ax.legend(frameon=False, fontsize=base_pt, labelcolor=style.axis_color,
                           loc="best")
        for text in legend.get_texts():
            text.set_fontname(font)

    _draw_data_labels(ax, chart_type, series, x, style, font, base_pt, unit_suffix,
                      chart_data)


def _cartesian_furniture(ax, categories, chart_data, style, font, base_pt,
                         unit_suffix, x, *, zero_baseline=True,
                         horizontal=False) -> None:
    """
    The axes, spines, gridlines and titles every cartesian chart shares.

    _draw_cartesian grew these inline, which was fine while it was the only
    caller. The bar family needs exactly the same treatment - a stacked chart
    whose gridlines ignore the deck's tokens is as obviously foreign as a
    plain bar chart whose do - and the deck convention meaning three slightly
    different things is precisely how "barh" came to be named in one line of
    spine logic and nowhere else.

    ``horizontal`` exchanges the axes: the value runs along x and the
    categories down y. Everything here that names an axis therefore asks
    rather than assuming, which is the whole reason this is one function.
    """
    value_axis = ax.xaxis if horizontal else ax.yaxis
    category_axis = ax.yaxis if horizontal else ax.xaxis
    value_side = "bottom" if horizontal else "left"
    category_side = "left" if horizontal else "bottom"

    if categories:
        # At a small size only every nth category is labelled. Eight labels
        # across three inches is eight labels on top of each other, and a
        # reader who can see one in three still knows what the axis is.
        stride = _tick_stride(len(x), chart_data.get("_maxTicks"))
        shown = [
            categories[i] if i < len(categories) and i % stride == 0 else ""
            for i in range(len(x))
        ]
        category_axis.set_ticks(x)
        category_axis.set_ticklabels(shown, color=style.axis_color,
                                     fontname=font, fontsize=base_pt)
    ax.tick_params(colors=style.axis_color, labelsize=base_pt)
    for label in (ax.get_xticklabels() if horizontal else ax.get_yticklabels()):
        label.set_fontname(font)

    # Reading order runs down the page, so the first category belongs at the
    # top. matplotlib counts y upwards, which would otherwise print a ranked
    # list upside down.
    if horizontal:
        ax.invert_yaxis()

    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.spines[value_side].set_visible(style.show_value_axis)
    # The category axis is also the line the bars grow from, so a deck that
    # draws a baseline keeps it even where it hides the axis itself.
    ax.spines[category_side].set_visible(
        style.show_category_axis or style.baseline_rule
    )
    for side in (value_side, category_side):
        ax.spines[side].set_color(style.axis_color)

    if not style.show_value_axis:
        ax.tick_params(**{"axis": "x" if horizontal else "y",
                          value_side: False, f"label{value_side}": False})
    if not style.show_category_axis:
        # The tick marks and the rule go; the category *names* stay. The two
        # axes are not symmetric: a value axis is redundant on a chart that
        # prints its values on the bars, so hiding it loses nothing, while
        # the category labels are the only thing saying which bar is which.
        # Dropping them turned a sourced three-segment forecast into three
        # unlabelled green rectangles over an axis titled "Market Segments".
        ax.tick_params(**{"axis": "y" if horizontal else "x",
                          category_side: False,
                          f"label{category_side}": True})

    _set_grid(value_axis, style.show_value_gridlines, style)
    _set_grid(category_axis, style.show_category_gridlines, style)
    ax.set_axisbelow(True)
    value_axis.set_major_formatter(_axis_formatter(unit_suffix))

    if zero_baseline:
        if horizontal:
            ax.set_xlim(left=min(0.0, ax.get_xlim()[0]))
        else:
            ax.set_ylim(bottom=min(0.0, ax.get_ylim()[0]))

    category_label = str(
        chart_data.get("xAxisTitle") or chart_data.get("categoryAxisTitle") or ""
    ).strip()
    if category_label:
        set_category_label = ax.set_ylabel if horizontal else ax.set_xlabel
        set_category_label(category_label, color=style.axis_color, fontname=font,
                           fontsize=base_pt, labelpad=base_pt * 0.5)


def _stack_legend(ax, style, font, base_pt) -> None:
    """
    A stack always names its bands.

    Unlike a plain bar chart, where a single series' legend would only repeat
    the axis label, every band of a stack is a different thing and the chart
    is unreadable without saying which. So this ignores the one-series rule
    and only honours an explicit "none".
    """
    if style.legend_style == "none":
        return
    legend = ax.legend(frameon=False, fontsize=base_pt,
                       labelcolor=style.axis_color, loc="upper left",
                       bbox_to_anchor=(1.01, 1.0), borderaxespad=0.0)
    for text in legend.get_texts():
        text.set_fontname(font)


def _draw_stacked(ax, chart_type, series, categories, chart_data, style, font,
                  base_pt, unit_suffix) -> None:
    """
    Segments stacked into one bar per category.

    Two charts share this code because they are the same drawing over
    different arithmetic. ``stacked`` shows what each total is made of;
    ``stacked100`` normalises every bar to the same height so only the
    composition is left, which is the chart for the case a plain total hides -
    revenue flat year on year while the mix underneath it moved decisively.

    Segments past MAX_STACK_SEGMENTS are grouped rather than drawn, for the
    same reason a pie groups its tail: the bands become thinner than their own
    labels, and a chart nobody can read is worse than one that says "Other".
    """
    n_points = len(categories) if categories else max(len(s["values"]) for s in series)
    x = list(range(n_points))
    bands = _group_stack_tail(series, n_points)

    percent = chart_type == "stacked100"
    columns = [[_finite(b["values"], i) for b in bands] for i in range(n_points)]
    totals = [sum(column) for column in columns]
    if percent:
        # A bar whose segments sum to nothing has no composition to show. Left
        # at zero it renders as an empty column, which reads as "this category
        # is zero" rather than "we have no breakdown for it".
        columns = [
            [(v / total * 100.0) if total else 0.0 for v in column]
            for column, total in zip(columns, totals)
        ]

    width = max(0.05, min(1.0, style.bar_thickness))
    bottoms = [0.0] * n_points
    for index, band in enumerate(bands):
        heights = [columns[point][index] for point in range(n_points)]
        color = style.color_sequence[index % len(style.color_sequence)]
        ax.bar(x, heights, bottom=bottoms, width=width,
               label=str(band.get("name") or f"Series {index + 1}"), color=color)
        _label_stack_band(ax, x, heights, bottoms, style, font, base_pt,
                          "%" if percent else unit_suffix, percent, color)
        bottoms = [bottoms[i] + heights[i] for i in range(n_points)]

    _cartesian_furniture(ax, categories, chart_data, style, font, base_pt,
                         "%" if percent else unit_suffix, x)
    if percent:
        # Fixed rather than data-derived: the point of the chart is that every
        # bar is the same height, and letting matplotlib pick 0-99.7 because
        # of a rounding residue undoes exactly that.
        ax.set_ylim(0, 100)
    _stack_legend(ax, style, font, base_pt)


def _group_stack_tail(series, n_points) -> list[dict]:
    """Collapse the smallest bands into one, past what a stack can show."""
    if len(series) <= MAX_STACK_SEGMENTS:
        return series
    ranked = sorted(
        series,
        key=lambda s: sum(abs(_finite(s["values"], i)) for i in range(n_points)),
        reverse=True,
    )
    keep = ranked[:MAX_STACK_SEGMENTS - 1]
    rest = ranked[MAX_STACK_SEGMENTS - 1:]
    other = {
        "name": "Other",
        "values": [sum(_finite(s["values"], i) for s in rest)
                   for i in range(n_points)],
    }
    # Back into the caller's order, so the stack reads the way it was stated.
    kept = [s for s in series if s in keep]
    return [*kept, other]


def _label_stack_band(ax, x, heights, bottoms, style, font, base_pt,
                      unit_suffix, percent, band_color) -> None:
    """
    A figure inside each band, where the band is tall enough to hold one.

    Printing every band's value unconditionally is how a stacked chart becomes
    a smear of overlapping numbers; a band under a couple of type heights
    simply cannot carry a label, and the legend plus the axis already say what
    it is.
    """
    if style.label_mode == "none":
        return
    span = max(sum(pair) for pair in zip(heights, bottoms)) or 1.0
    floor = span * 0.08
    # The label sits on the band, not on the slide, so it is the one piece of
    # chart text that cannot take axis_color: a dark figure on a dark band is
    # invisible however correct the deck's tokens are.
    colour = style.stack_label_color or _readable_on(band_color)
    for index, height in enumerate(heights):
        if height <= floor:
            continue
        # Whole percentages on a 100% stack. _format_number keeps a decimal
        # for anything that is not exactly integral, so a column summing to
        # 99.999 through floating point printed "30%" beside "29.0%" - one
        # chart using two conventions, in labels a reader compares directly.
        shown = (f"{height:.0f}" if percent
                 else _format_number(height))
        ax.annotate(
            f"{shown}{unit_suffix}",
            xy=(x[index], bottoms[index] + height / 2.0),
            ha="center", va="center", color=colour,
            fontname=font, fontsize=base_pt * 0.8,
        )


def _draw_waterfall(ax, series, categories, chart_data, style, font, base_pt,
                    unit_suffix) -> None:
    """
    Where the movement between two totals came from.

    The bridge. Two bars sit on the baseline - the opening and closing totals -
    and the ones between float at the level the running total has reached, so
    the eye follows a staircase from one end to the other.

    Which bars are totals is stated, not guessed: ``totalIndices`` on the chart,
    defaulting to the first and last. Guessing from the numbers is how a
    genuine mid-series total gets drawn floating, and the reader has no way to
    tell that happened.

    Rises and falls are coloured apart, because the sign is the whole content
    of the chart and asking a reader to read it off the direction of a bar
    they have to compare against its neighbour is asking too much.
    """
    values = [_finite(series[0]["values"], i) for i in range(len(categories) or
                                                            len(series[0]["values"]))]
    n_points = len(values)
    x = list(range(n_points))
    totals = _total_indices(chart_data, n_points)

    # Running levels first, so the frame can be chosen before anything is
    # drawn. The order matters: the anchor bars are drawn from the frame's
    # floor, and the frame is decided by where the movements sit.
    running = 0.0
    levels = []
    for index, value in enumerate(values):
        if index in totals:
            levels.append((0.0, value))
            running = value
        else:
            base = running if value >= 0 else running + value
            levels.append((base, abs(value)))
            running += value

    floor, ceiling = _waterfall_frame(levels, totals)
    ax.set_ylim(floor, ceiling)

    width = max(0.05, min(1.0, style.bar_thickness))
    bottoms, heights, colors = [], [], []
    for index, value in enumerate(values):
        if index in totals:
            # From the frame's floor, not from zero. A bridge whose anchors are
            # drawn from zero forces the axis to include zero, and a movement
            # of 72 against a total of 4,104 is then under 2% of the plot
            # height - four coloured slivers along the top, and a chart that
            # says nothing. Every published bridge does it this way.
            bottoms.append(floor)
            heights.append(value - floor)
            colors.append(style.waterfall_total_color)
        else:
            base, height = levels[index]
            bottoms.append(base)
            heights.append(height)
            colors.append(
                style.waterfall_rise_color if value >= 0
                else style.waterfall_fall_color
            )

    ax.bar(x, heights, bottom=bottoms, width=width, color=colors)
    _connect_waterfall(ax, x, bottoms, heights, values, totals, width, style)

    # Not conditional on label_mode: a framed axis means the bar lengths no
    # longer state the figures, so the labels ARE the figures. A deck whose
    # convention is "no data labels" still gets them here, because the
    # alternative is a chart with no numbers on it at all.
    for index, value in enumerate(values):
        top = bottoms[index] + heights[index]
        sign = "" if index in totals or value < 0 else "+"
        ax.annotate(
            f"{sign}{_format_number(value)}{unit_suffix}",
            xy=(x[index], top),
            xytext=(0, base_pt * 0.4), textcoords="offset points",
            ha="center", va="bottom",
            color=style.axis_color, fontname=font, fontsize=base_pt * 0.85,
        )

    _cartesian_furniture(ax, categories, chart_data, style, font, base_pt,
                         unit_suffix, x, zero_baseline=False)
    # _cartesian_furniture may have widened the limits; the frame is the
    # decision here and it wins.
    ax.set_ylim(floor, ceiling)


def _waterfall_frame(levels, totals) -> tuple[float, float]:
    """
    The value range a bridge is drawn in.

    Chosen from where the bars actually sit rather than forced to zero, which
    is the one place in this module that departs from "a bar's length is its
    value". It is honest here in a way it would not be on a comparison chart,
    because a waterfall's movements encode differences positioned at levels
    rather than lengths from an origin - the reader follows the connectors and
    reads the labels.

    Zero is still included whenever the data comes near it, so a build-up from
    nothing looks like one rather than being needlessly zoomed.
    """
    tops = [base + height for base, height in levels]
    bases = [
        base for index, (base, _height) in enumerate(levels)
        if index not in totals
    ]
    high = max(tops) if tops else 1.0
    low = min([*bases, *tops]) if bases else min(tops or [0.0])

    span = (high - low) or (abs(high) or 1.0)
    # A bridge whose lowest movement is already near zero should show zero:
    # zooming a build-up from nothing hides that it started at nothing.
    if 0.0 < low < span * 0.75:
        return -span * 0.04, high + span * 0.18

    # Room below the lowest level so the anchor columns read as anchors. With
    # only label headroom the floor sat just under the opening total and the
    # anchors came out as 8% stubs beside movements twice their height, which
    # inverts what the chart is saying - the totals are the thing being
    # bridged. This much leaves the anchors and the movements comparable, and
    # the anchors visibly entering from the bottom edge, which is how a
    # zoomed bridge is conventionally drawn.
    return low - span * ANCHOR_ROOM, high + span * 0.18


def _total_indices(chart_data: dict, n_points: int) -> set[int]:
    """Which bars sit on the baseline rather than floating."""
    declared = chart_data.get("totalIndices")
    if isinstance(declared, list):
        indices = set()
        for value in declared:
            try:
                index = int(value)
            except (TypeError, ValueError):
                continue
            if 0 <= index < n_points:
                indices.add(index)
        if indices:
            return indices
    if n_points <= 1:
        return {0} if n_points else set()
    return {0, n_points - 1}


def _connect_waterfall(ax, x, bottoms, heights, values, totals, width,
                       style) -> None:
    """
    The hairlines joining each bar to the next.

    Without them the bars are a row of floating rectangles and the reader has
    to reconstruct the staircase themselves, which is the one thing the chart
    was supposed to do for them.

    Drawn above the gridlines. ``set_axisbelow(True)`` puts the grid at zorder
    0.5, so a connector below that vanishes wherever its level happens to
    coincide with a gridline - the one connector on a build-up from 40 with
    ticks every 20 was invisible, and only that one, which reads as a missing
    hairline rather than as an occlusion.
    """
    for index in range(len(x) - 1):
        level = (bottoms[index] + heights[index]
                 if values[index] >= 0 or index in totals else bottoms[index])
        ax.plot(
            [x[index] + width / 2, x[index + 1] - width / 2], [level, level],
            color=style.axis_color, linewidth=0.8, linestyle=(0, (3, 3)),
            zorder=0.9,
        )


def _readable_on(background: str) -> str:
    """Whichever of white or the deck's dark reads against this band."""
    from app import colors

    if not background or not colors.parse_hex(background):
        return "#FFFFFF"
    return colors.readable_on(background, "#FFFFFF")


def _finite(values, index: int) -> float:
    """One value from a series, with NaN and short series read as zero."""
    if index >= len(values):
        return 0.0
    value = values[index]
    return 0.0 if value != value else float(value)


def _draw_slope(ax, series, categories, chart_data, style, font, base_pt,
                unit_suffix) -> None:
    """
    Who moved up and who moved down between two dates.

    Each series is one entity - a segment, a competitor, a region - and the
    categories are the moments compared. A line per entity, labelled at both
    ends with its own name and value, so the crossing lines *are* the finding.

    A bar chart of the same data shows eight bars and leaves the reader to
    subtract pairs; the slope chart shows the reordering directly, which is
    the only reason to draw one.

    Direction is coloured, not the entity: rising and falling is what the chart
    is about, and a palette that gave each entity its own colour would make the
    reader look up a legend to learn something the line's angle already said.
    """
    n_points = len(categories) if categories else max(len(s["values"]) for s in series)
    x = list(range(n_points))

    for entry in series:
        values = [_finite(entry["values"], i) for i in range(n_points)]
        name = str(entry.get("name") or "")
        rose = values[-1] >= values[0]
        colour = (style.waterfall_rise_color if rose
                  else style.waterfall_fall_color)
        ax.plot(x, values, color=colour, linewidth=2.0, marker="o",
                markersize=base_pt * 0.42, zorder=3)

        if style.label_mode == "none":
            continue
        # Name and value on the left, value on the right: repeating the name at
        # both ends doubles the text on a chart whose whole point is that the
        # lines are followable.
        ax.annotate(
            f"{name}  {_format_number(values[0])}{unit_suffix}",
            xy=(x[0], values[0]), xytext=(-base_pt * 0.5, 0),
            textcoords="offset points", ha="right", va="center",
            color=style.axis_color, fontname=font, fontsize=base_pt * 0.85,
        )
        ax.annotate(
            f"{_format_number(values[-1])}{unit_suffix}",
            xy=(x[-1], values[-1]), xytext=(base_pt * 0.5, 0),
            textcoords="offset points", ha="left", va="center",
            color=colour, fontname=font, fontsize=base_pt * 0.85,
        )

    _cartesian_furniture(ax, categories, chart_data, style, font, base_pt,
                         unit_suffix, x, zero_baseline=False)
    # The end labels sit outside the plotted points, so the category axis has
    # to make room or they are clipped at the figure edge.
    ax.set_xlim(-0.55, n_points - 1 + 0.55)


def _draw_dumbbell(ax, series, categories, chart_data, style, font, base_pt,
                   unit_suffix) -> None:
    """
    How big the gap between two states is, per category.

    Two series - plan and actual, us and the peer group, this year and last -
    drawn as a dot each, joined by the bar that *is* the gap. Grouped bars of
    the same data ask the reader to measure the difference between two bar
    heights; here the difference is the drawn object.

    Horizontal, because the categories are things with names rather than dates,
    and the same reasoning that makes barh right for a ranked list applies.

    Given one series it draws a dot plot instead of nothing. build_chart
    refuses that shape at the point it is stated, but a chart written inline
    can still reach here, and a dot plot is what one series of this data
    honestly is - a missing exhibit is a worse outcome than a plainer one.
    """
    n_points = len(categories) if categories else max(len(s["values"]) for s in series)
    y = list(range(n_points))
    first = [_finite(series[0]["values"], i) for i in range(n_points)]
    if len(series) < 2:
        _draw_dot_plot(ax, series[0], first, y, categories, chart_data, style,
                       font, base_pt, unit_suffix)
        return
    second = [_finite(series[1]["values"], i) for i in range(n_points)]

    for index in range(n_points):
        low, high = sorted((first[index], second[index]))
        ax.plot([low, high], [index, index], color=style.grid_color,
                linewidth=base_pt * 0.32, solid_capstyle="round", zorder=1)

    for entry, values, colour_index in (
        (series[0], first, 0), (series[1], second, 1),
    ):
        colour = style.color_sequence[colour_index % len(style.color_sequence)]
        ax.scatter(values, y, s=base_pt * 11, color=colour, zorder=3,
                   label=str(entry.get("name") or ""))

    if style.label_mode != "none":
        for index in range(n_points):
            gap = second[index] - first[index]
            end = max(first[index], second[index])
            ax.annotate(
                f"{'+' if gap >= 0 else ''}{_format_number(gap)}{unit_suffix}",
                xy=(end, index), xytext=(base_pt * 0.6, 0),
                textcoords="offset points", ha="left", va="center",
                color=style.axis_color, fontname=font, fontsize=base_pt * 0.8,
            )

    _cartesian_furniture(ax, categories, chart_data, style, font, base_pt,
                         unit_suffix, y, horizontal=True)
    left, right = ax.get_xlim()
    ax.set_xlim(left, right + (right - left) * 0.14)
    _side_legend(ax, style, font, base_pt)


def _draw_dot_plot(ax, entry, values, y, categories, chart_data, style, font,
                   base_pt, unit_suffix) -> None:
    """One series of named categories, as a dot each. What a half dumbbell is."""
    ax.scatter(values, y, s=base_pt * 11,
               color=style.color_sequence[0], zorder=3,
               label=str(entry.get("name") or ""))
    if style.label_mode != "none":
        for index, value in enumerate(values):
            ax.annotate(
                f"{_format_number(value)}{unit_suffix}",
                xy=(value, index), xytext=(base_pt * 0.6, 0),
                textcoords="offset points", ha="left", va="center",
                color=style.axis_color, fontname=font, fontsize=base_pt * 0.8,
            )
    _cartesian_furniture(ax, categories, chart_data, style, font, base_pt,
                         unit_suffix, y, horizontal=True)
    left, right = ax.get_xlim()
    ax.set_xlim(left, right + (right - left) * 0.14)


def _draw_bullet(ax, series, categories, chart_data, style, font, base_pt,
                 unit_suffix) -> None:
    """
    Whether each measure is hitting its target.

    A bar for the actual, a rule for the target, a pale band behind for the
    scale being judged on. The KPI scorecard row, which a grouped bar chart
    renders as "actual and target side by side" - readable, but it makes the
    reader compare two heights to answer a yes/no question.

    **Every row is scaled to its own target, not to a shared axis.** This is
    the opposite of the small-multiples rule and it is right for the opposite
    reason: the rows are different measures in different units. A rendered
    deck made it obvious - revenue of 4.12, margin of 31 and NPS of 44 on one
    shared axis drew the revenue bar as an invisible stub with its target rule
    on top of it, and the one row a reader most wanted was the one they could
    not see. What a bullet row compares is progress against *its own* target,
    so that is what the width encodes.

    The value axis is therefore hidden outright: it has no single meaning once
    each row has its own scale, and drawing it would invite reading one row's
    length against another's. The labels carry the figures, and they carry
    both the actual and the target so the comparison is stated in words as
    well as drawn.

    Targets are stated on the chart rather than derived from a second series,
    because a target is not a measurement and drawing it as one is the same
    category error as filling a projection.
    """
    n_points = len(categories) if categories else len(series[0]["values"])
    y = list(range(n_points))
    actual = [_finite(series[0]["values"], i) for i in range(n_points)]
    targets = _bullet_targets(chart_data, series, n_points)

    # One row, one scale. Headroom past whichever of actual and target is
    # larger, so a measure that beat its target still has its bar inside the
    # band rather than running off the end of it.
    scales = [
        max(abs(actual[i]), abs(targets[i] or 0.0)) * 1.25 or 1.0
        for i in range(n_points)
    ]
    fractions = [
        max(0.0, min(1.0, actual[i] / scales[i])) for i in range(n_points)
    ]

    bar_h = max(0.05, min(0.8, style.bar_thickness)) * 0.42
    band_h = bar_h * 2.3

    ax.barh(y, [1.0] * n_points, height=band_h, color=style.grid_color,
            alpha=0.30, zorder=0)
    ax.barh(y, fractions, height=bar_h, color=style.color_sequence[0], zorder=2,
            label=str(series[0].get("name") or ""))

    for index, target in enumerate(targets):
        if target is None:
            continue
        at = max(0.0, min(1.0, target / scales[index]))
        ax.plot([at, at], [index - band_h / 2, index + band_h / 2],
                color=style.waterfall_total_color, linewidth=2.2, zorder=4,
                solid_capstyle="butt")

    for index, value in enumerate(actual):
        target = targets[index]
        text = f"{_format_number(value)}{unit_suffix}"
        if target is not None:
            text += f" of {_format_number(target)}{unit_suffix}"
        met = target is not None and value >= target
        ax.annotate(
            text, xy=(1.0, index), xytext=(base_pt * 0.6, 0),
            textcoords="offset points", ha="left", va="center",
            color=(style.waterfall_rise_color if met
                   else style.waterfall_fall_color) if target is not None
            else style.axis_color,
            fontname=font, fontsize=base_pt * 0.82,
        )

    ax.set_yticks(y)
    ax.set_yticklabels(categories[:n_points] or [str(i) for i in y],
                       color=style.axis_color, fontname=font, fontsize=base_pt)
    ax.invert_yaxis()
    ax.set_ylim(n_points - 0.5, -0.5)
    # Room to the right for the "4.12 of 4.0" labels, which sit outside the band.
    ax.set_xlim(0.0, 1.42)
    ax.tick_params(colors=style.axis_color, labelsize=base_pt, length=0)
    for side in ("top", "right", "bottom"):
        ax.spines[side].set_visible(False)
    ax.spines["left"].set_visible(style.show_category_axis or style.baseline_rule)
    ax.spines["left"].set_color(style.axis_color)
    # No value axis and no gridlines, by construction: each row has its own
    # scale, so a shared axis would be a scale that describes none of them.
    ax.tick_params(axis="x", bottom=False, labelbottom=False)
    ax.grid(False)


def _bullet_targets(chart_data: dict, series, n_points: int) -> list:
    """
    The target for each measure, or None where none was given.

    Read from ``targets`` on the chart, or from a series explicitly named as
    the target. A measure with no target still draws - it is a bar with no
    rule across it, which is honest - rather than being dropped or given an
    invented one.
    """
    declared = chart_data.get("targets")
    if not isinstance(declared, list):
        for entry in series[1:]:
            if "target" in str(entry.get("name") or "").lower():
                declared = entry.get("values")
                break
    out = []
    for index in range(n_points):
        value = None
        if isinstance(declared, list) and index < len(declared):
            try:
                candidate = float(declared[index])
                value = candidate if candidate == candidate else None
            except (TypeError, ValueError):
                value = None
        out.append(value)
    return out


def _side_legend(ax, style, font, base_pt) -> None:
    """A legend beside the plot, for charts whose series are not interchangeable."""
    if style.legend_style == "none":
        return
    legend = ax.legend(frameon=False, fontsize=base_pt,
                       labelcolor=style.axis_color, loc="upper left",
                       bbox_to_anchor=(1.01, 1.0), borderaxespad=0.0)
    for text in legend.get_texts():
        text.set_fontname(font)


def _draw_heatmap(ax, series, categories, chart_data, style, font, base_pt,
                  unit_suffix) -> None:
    """
    Where the hot spots are in a dense matrix.

    One row per series, one column per category, each cell shaded by its value.
    The chart for a risk matrix, a cohort retention grid, a coverage table -
    data that is genuinely two-dimensional and that a bar chart can only show
    one slice of at a time.

    The ramp is built from the deck's own accent rather than a library
    colormap. matplotlib's defaults (viridis, coolwarm) are excellent and
    belong to no deck; a heatmap in someone else's palette is the single most
    obvious way a generated exhibit announces that it was generated.

    Each cell carries its figure, in whichever of light or dark reads against
    the shade behind it - the one place in this module where a text colour has
    to be computed per element rather than taken from a token.
    """
    from app import colors

    rows = [str(entry.get("name") or "") for entry in series]
    n_columns = len(categories) if categories else max(
        len(entry["values"]) for entry in series
    )
    grid = [[_finite(entry["values"], i) for i in range(n_columns)]
            for entry in series]

    flat = [value for row in grid for value in row]
    low, high = (min(flat), max(flat)) if flat else (0.0, 1.0)
    span = (high - low) or 1.0
    cold = style.heatmap_low_color
    hot = style.heatmap_high_color or style.color_sequence[0]

    for r, row in enumerate(grid):
        for c, value in enumerate(row):
            shade = colors.mix(cold, hot, (value - low) / span)
            ax.add_patch(plt.Rectangle((c - 0.5, r - 0.5), 1.0, 1.0,
                                       facecolor=shade, edgecolor="none"))
            if style.label_mode == "none":
                continue
            ax.annotate(
                f"{_format_number(value)}{unit_suffix}",
                xy=(c, r), ha="center", va="center",
                color=colors.readable_on(shade, "#FFFFFF"),
                fontname=font, fontsize=base_pt * 0.8,
            )

    ax.set_xlim(-0.5, n_columns - 0.5)
    ax.set_ylim(-0.5, len(grid) - 0.5)
    ax.set_xticks(range(n_columns))
    ax.set_xticklabels(categories or [str(i) for i in range(n_columns)],
                       color=style.axis_color, fontname=font, fontsize=base_pt)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels(rows, color=style.axis_color, fontname=font,
                       fontsize=base_pt)
    ax.invert_yaxis()
    ax.tick_params(colors=style.axis_color, labelsize=base_pt, length=0)
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(False)
    # A grid drawn over filled cells is a grid drawn over the data. The cells
    # are their own structure, and the deck's gridline tokens do not apply.
    ax.grid(False)


def _draw_combo(ax, series, categories, chart_data, style, font, base_pt,
                unit_suffix) -> None:
    """
    A volume and a rate that only mean something together.

    Revenue in bars with margin as a line over it; headcount with cost per
    head; units shipped with average price. Two measures on two scales, which
    is the one legitimate use of a second axis - and the reason to build it
    deliberately rather than let a caller put percentages and billions on one
    axis and get a flat line along the bottom.

    Which series belong on the right is stated, never inferred. Guessing from
    magnitudes works until a rate happens to be larger than a volume, and the
    chart is then wrong in a way that looks fine.
    """
    n_points = len(categories) if categories else max(
        len(entry["values"]) for entry in series
    )
    x = list(range(n_points))
    right_names = _secondary_series(chart_data, series)

    left = [s for s in series if str(s.get("name") or "") not in right_names]
    right = [s for s in series if str(s.get("name") or "") in right_names]

    for index, entry in enumerate(left):
        values = [_finite(entry["values"], i) for i in range(n_points)]
        colour = style.color_sequence[index % len(style.color_sequence)]
        bar_width, offsets = _bar_geometry(style, len(left), index, x)
        ax.bar(offsets, values, width=bar_width, color=colour,
               label=str(entry.get("name") or ""))

    _cartesian_furniture(ax, categories, chart_data, style, font, base_pt,
                         unit_suffix, x)

    if not right:
        # Nothing declared for the right-hand axis, and nothing to put there.
        # Drawing an empty twin gives the chart a second set of ticks
        # measuring nothing, which is worse than the bar chart this is.
        _side_legend(ax, style, font, base_pt)
        return

    twin = ax.twinx()
    twin.set_facecolor("none")
    for index, entry in enumerate(right):
        values = [_finite(entry["values"], i) for i in range(n_points)]
        colour = style.color_sequence[(len(left) + index) % len(style.color_sequence)]
        twin.plot(x, values, color=colour, linewidth=2.4, marker="o",
                  markersize=base_pt * 0.42,
                  label=str(entry.get("name") or ""))
        if style.label_mode != "none":
            twin.annotate(
                f"{_format_number(values[-1])}{_secondary_suffix(chart_data)}",
                xy=(x[-1], values[-1]), xytext=(base_pt * 0.4, 0),
                textcoords="offset points", ha="left", va="center",
                color=colour, fontname=font, fontsize=base_pt * 0.85,
            )

    # The second axis is only legible if it says what it measures, so it keeps
    # its ticks even on a deck whose convention is to drop the first one.
    twin.tick_params(colors=style.axis_color, labelsize=base_pt)
    for label in twin.get_yticklabels():
        label.set_fontname(font)
    for side in ("top", "left", "bottom"):
        twin.spines[side].set_visible(False)
    twin.spines["right"].set_color(style.axis_color)
    twin.yaxis.set_major_formatter(_axis_formatter(_secondary_suffix(chart_data)))
    twin.grid(False)

    secondary_label = str(chart_data.get("secondaryAxisTitle") or "").strip()
    if secondary_label:
        twin.set_ylabel(secondary_label, color=style.axis_color, fontname=font,
                        fontsize=base_pt, labelpad=base_pt * 0.6)

    # One legend for both axes: two legends on one chart is two things to read
    # before the chart itself.
    handles = ax.get_legend_handles_labels()
    twin_handles = twin.get_legend_handles_labels()
    if style.legend_style != "none":
        legend = ax.legend(
            handles[0] + twin_handles[0], handles[1] + twin_handles[1],
            frameon=False, fontsize=base_pt, labelcolor=style.axis_color,
            loc="upper left", bbox_to_anchor=(0.0, 1.14), ncol=2,
            borderaxespad=0.0,
        )
        for text in legend.get_texts():
            text.set_fontname(font)


def _secondary_series(chart_data: dict, series) -> set[str]:
    """
    Which series belong on the right-hand axis.

    Stated, never inferred. Guessing from magnitudes works until a rate is
    larger than a volume, and the chart is then wrong in a way that looks fine.
    Nothing declared means nothing on the right, and the last series is the
    line - which is what a caller asking for "combo" with two series means.
    """
    declared = chart_data.get("secondaryAxisSeries")
    if isinstance(declared, list) and declared:
        return {str(name) for name in declared}
    if isinstance(declared, str) and declared.strip():
        return {declared.strip()}
    if len(series) >= 2:
        return {str(series[-1].get("name") or "")}
    return set()


def _secondary_suffix(chart_data: dict) -> str:
    return str(chart_data.get("secondaryValueSuffix") or "").strip()


def _render_small_multiples(
    chart_data, series, categories, style, font, unit_suffix,
    width_in, height_in, dpi, slide_title,
) -> bytes | None:
    """
    The same chart repeated per segment, on one shared scale.

    This is the exhibit that answers a question no single chart can: does the
    pattern hold everywhere, or is one segment carrying it? Four regional
    trend lines overlaid on one axes is a tangle; the same four as a row of
    panels is readable at a glance.

    **The shared scale is the whole thing, and it is enforced here rather than
    offered.** Panels scaled independently are the classic small-multiple lie:
    four charts that look alike and are not comparable, where the one with the
    smallest numbers has the steepest-looking line. Nothing in this function
    lets a caller opt out of it.

    Its own figure rather than a branch inside the single-axes path, because a
    grid is a grid of axes and everything below the dispatch assumes one.
    """
    panels = series[:MAX_PANELS]
    if not panels:
        return None

    columns = min(len(panels), 2 if len(panels) <= 4 else 3)
    rows = math.ceil(len(panels) / columns)
    panel_type = str(chart_data.get("panelType") or "line").lower()
    if panel_type not in ("line", "bar", "area"):
        panel_type = "line"

    # One domain across every panel. Computed before anything is drawn, so no
    # panel can be autoscaled to its own data.
    flat = [v for entry in panels for v in entry["values"] if v == v]
    if not flat:
        return None
    low, high = min(flat), max(flat)
    if panel_type in ("bar", "area"):
        low = min(0.0, low)
    pad = (high - low) * 0.12 or (abs(high) * 0.12 or 1.0)

    # Panel type is sized against the panel, not the figure: a sixth of a
    # chart's width cannot carry a chart's type size.
    base_pt = max(6.5, min(11.0, 3.6 * math.sqrt(
        (width_in / columns) * (height_in / rows) / 6.0
    ) + 5.0))

    fig = None
    try:
        fig, axes = plt.subplots(
            rows, columns, figsize=(width_in, height_in), dpi=dpi,
            sharex=True, sharey=True, squeeze=False,
        )
        fig.patch.set_alpha(0.0)
        flat_axes = [a for row in axes for a in row]

        for index, entry in enumerate(panels):
            ax = flat_axes[index]
            ax.set_facecolor("none")
            values = list(entry["values"])
            x = list(range(len(values)))
            # Each panel gets its own colour so a reader can pair it with a
            # legend elsewhere on the slide, but the *scale* never varies.
            colour = style.color_sequence[index % len(style.color_sequence)]

            if panel_type == "bar":
                ax.bar(x, values, color=colour,
                       width=max(0.05, min(1.0, style.bar_thickness)))
            else:
                ax.plot(x, values, color=colour, linewidth=2.0)
                if panel_type == "area":
                    ax.fill_between(x, values, color=colour, alpha=0.22)

            ax.set_ylim(low - pad, high + pad)
            _panel_furniture(ax, entry, categories, style, font, base_pt,
                             unit_suffix, x,
                             show_value_axis=index % columns == 0,
                             show_categories=index >= len(panels) - columns)

        # An empty cell is a hole in the grid, not a panel with nothing in it.
        for ax in flat_axes[len(panels):]:
            ax.set_visible(False)

        _apply_title(fig, flat_axes[0], chart_data, slide_title, style, font,
                     base_pt * 1.25)
        fig.set_layout_engine("constrained")

        buf = io.BytesIO()
        fig.savefig(buf, format="png", transparent=True)
        return buf.getvalue()
    except Exception as e:
        logger.warning("chart_render_failed", chart_type="smallmultiples",
                       error=str(e))
        return None
    finally:
        if fig is not None:
            plt.close(fig)


def _panel_furniture(ax, entry, categories, style, font, base_pt, unit_suffix,
                     x, *, show_value_axis: bool, show_categories: bool) -> None:
    """
    One panel's own furniture, which is deliberately less than a chart's.

    Repeating the value axis on every panel spends a sixth of each one saying
    the same numbers, and the axis is shared - so only the leftmost column
    draws it, and only the bottom row draws the category labels. That is the
    convention every published small-multiple grid uses, and it is what makes
    the panels small enough to be worth having.
    """
    ax.set_title(str(entry.get("name") or ""), color=style.axis_color,
                 fontname=font, fontsize=base_pt, loc="left", pad=base_pt * 0.4)

    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.spines["left"].set_visible(show_value_axis and style.show_value_axis)
    ax.spines["bottom"].set_visible(style.show_category_axis or style.baseline_rule)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(style.axis_color)

    ax.tick_params(colors=style.axis_color, labelsize=base_pt * 0.85, length=2)
    if not (show_value_axis and style.show_value_axis):
        ax.tick_params(axis="y", left=False, labelleft=False)
    if show_categories and categories:
        ax.set_xticks(x)
        ax.set_xticklabels(categories[:len(x)], color=style.axis_color,
                           fontname=font, fontsize=base_pt * 0.85)
    else:
        ax.tick_params(axis="x", bottom=False, labelbottom=False)

    _set_grid(ax.yaxis, style.show_value_gridlines, style)
    ax.set_axisbelow(True)
    ax.yaxis.set_major_formatter(_axis_formatter(unit_suffix))


def _draw_data_labels(
    ax, chart_type, series, x, style, font, base_pt, unit_suffix, chart_data,
) -> None:
    """Print each point's value next to it, where that stays readable."""
    if chart_data.get("showDataLabels") is False or style.label_mode == "none":
        return
    if chart_type == "scatter":
        return

    total_points = sum(len(s["values"]) for s in series)
    cap = int(chart_data.get("_maxLabelledPoints") or MAX_LABELLED_POINTS)
    crowded = total_points > cap * 2 or len(x) > cap
    # "auto" is what the renderer always did: label everything, unless there
    # is so much of it that the labels smear into each other. A declared mode
    # overrides that, because a deck whose convention is "label the end of
    # every bar" means it however many bars there are.
    if style.label_mode == "auto" and crowded:
        return

    horizontal = chart_type == "barh"
    labelled = _points_to_label(style, chart_type, x)
    for i, s in enumerate(series):
        bar_width, offsets = _bar_geometry(style, len(series), i, x)
        for position, (xi, value) in enumerate(zip(x, s["values"])):
            if value != value or position not in labelled:
                continue
            along = offsets[position] if chart_type in ("bar", "barh") else xi
            # A horizontal bar's value runs along x, so its label goes at the
            # end of the bar rather than above it - which is the whole reason
            # the orientation exists: the category label has the width it
            # needs and the figure is still at the bar's tip.
            ax.annotate(
                f"{_format_number(value)}{unit_suffix}",
                xy=(value, along) if horizontal else (along, value),
                # Offset in points, not data units: a fixed typographic gap
                # from the marker whatever the series' magnitude.
                xytext=(base_pt * 0.4, 0) if horizontal else (0, base_pt * 0.45),
                textcoords="offset points",
                ha="left" if horizontal else "center",
                va="center" if horizontal else "bottom",
                color=style.axis_color,
                fontname=font,
                fontsize=base_pt * 0.85,
            )

    # Labels sit past the end of the longest bar, so the value axis needs
    # room for them.
    if horizontal:
        left, right = ax.get_xlim()
        ax.set_xlim(left, right + (right - left) * 0.12)
    else:
        bottom, top = ax.get_ylim()
        ax.set_ylim(bottom, top + (top - bottom) * 0.12)


def render_table_to_png(
    table_data: dict,
    design_system: dict | None,
    width_in: float,
    height_in: float,
    dpi: int = 200,
) -> bytes | None:
    """
    Render a table block to a transparent-background PNG.

    A table is drawn as an image for the same reason a chart is: the
    primitive layer is a closed union of five kinds - text, rect, line,
    ellipse, image - and "there is no sixth" is load-bearing (see
    ``primitives.PRIMITIVE_KINDS``). A native PowerPoint table would be a
    sixth, so this reuses the escape hatch that already exists rather than
    widening the union.

    Column widths, row heights, numeric alignment and type size come from
    ``table_layout.plan_table`` - the same geometry the template-path
    renderer's native table uses, so a table looks the same shape whichever
    path drew it, and colours from ``table_style.resolve_table_style``, so an
    authored language's own palette (``system_for`` already populates
    ``dataViz``) reaches its tables the same way it reaches its charts.

    Deliberately narrower than the native-table renderer it sits beside:
    the hairline row-rule treatment and apparatus (footnote-marker)
    superscripts inside cells are not ported. Neither exists elsewhere on
    this rendering path yet either - no text primitive here resolves an
    apparatus marker - so a table not having them is consistent with
    everything around it rather than a gap unique to tables.
    """
    from app import colors as colors_module
    from app.renderer import table_layout, table_style as table_style_module

    if width_in <= 0 or height_in <= 0:
        return None
    raw_rows = table_data.get("rows") or []
    if not raw_rows:
        return None
    rows = [[str(cell) for cell in row] for row in raw_rows]
    n_cols = max((len(row) for row in rows), default=0)
    if n_cols == 0:
        return None
    header_row = bool(table_data.get("headerRow"))

    layout = table_layout.plan_table(rows, header_row, width_in, height_in)
    if layout is None:
        return None
    style = table_style_module.resolve_table_style(design_system)

    n_rows = len(rows)
    fig_height = max(0.1, layout.height_in)
    fig = plt.figure(figsize=(width_in, fig_height), dpi=dpi)
    fig.patch.set_alpha(0.0)
    ax = fig.add_axes((0.0, 0.0, 1.0, 1.0))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    col_edges = [0.0]
    for fraction in layout.col_fractions:
        col_edges.append(col_edges[-1] + fraction)
    row_h = 1.0 / n_rows
    header_rgb = style.header_bg
    header_fg = style.header_fg
    band_rgb = style.band_bg
    row_rgb = style.row_bg
    body_fg = "#1A1A1A"
    pad = 0.012

    for r_idx, row in enumerate(rows):
        is_header = header_row and r_idx == 0
        is_total = r_idx == layout.total_row_index
        top = 1.0 - r_idx * row_h
        if is_header:
            face = header_rgb
        elif is_total:
            face = colors_module.mix(band_rgb, header_rgb, 0.22)
        elif r_idx % 2 == 1:
            face = band_rgb
        else:
            face = row_rgb
        ax.add_patch(plt.Rectangle(
            (0.0, top - row_h), 1.0, row_h, transform=ax.transAxes,
            facecolor=face, edgecolor="none", zorder=0,
        ))
        size_pt = layout.header_pt if is_header else layout.body_pt
        fg = header_fg if is_header else body_fg
        for c_idx in range(n_cols):
            cell = row[c_idx] if c_idx < len(row) else ""
            if not cell:
                continue
            right_align = c_idx in layout.numeric_columns
            left, right = col_edges[c_idx], col_edges[c_idx + 1]
            x = right - pad if right_align else left + pad
            ax.text(
                x, top - row_h / 2, cell, transform=ax.transAxes,
                ha="right" if right_align else "left", va="center",
                fontsize=size_pt, fontname=style.font_name, color=fg,
                fontweight="bold" if (is_header or is_total) else "normal",
                clip_on=True,
            )

    buffer = io.BytesIO()
    try:
        fig.savefig(buffer, format="png", transparent=True, dpi=dpi)
    except Exception:  # pragma: no cover - a bad font falls back, never raises
        plt.close(fig)
        return None
    plt.close(fig)
    return buffer.getvalue()
