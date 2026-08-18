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
}

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

    chart_type = (chart_data.get("chartType") or chart_data.get("chart_type") or "bar").lower()
    if chart_type not in SUPPORTED_CHART_TYPES:
        chart_type = "bar"

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

    fig = None
    try:
        fig, ax = plt.subplots(figsize=(width_in, height_in), dpi=dpi)
        fig.patch.set_alpha(0.0)
        ax.set_facecolor("none")

        finish_legend = None
        if chart_type == "pie":
            _draw_pie(ax, series, categories, style, font, base_pt)
        elif chart_type == "bubble":
            finish_legend = _draw_bubble(ax, chart_data, style, font, base_pt)
        elif chart_type == "waterfall":
            _draw_waterfall(ax, series, categories, chart_data, style, font,
                            base_pt, unit_suffix)
        elif chart_type in ("stacked", "stacked100"):
            _draw_stacked(ax, chart_type, series, categories, chart_data, style,
                          font, base_pt, unit_suffix)
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


def _draw_bars_with_forecast(ax, offsets, values, measured, forecast_from,
                             bar_width, color, name, horizontal=False) -> None:
    """
    Bars, with any projected part drawn as an outline rather than a fill.

    A projection filled the same as a measurement is a chart asserting a
    figure it does not have. The outline is the visual claim "this part is
    extrapolated", and #125's footnote is the words for it - validation
    requires both.
    """
    solid = [
        max(0.0, min(value, measured[index])) if value == value else 0.0
        for index, value in enumerate(values)
    ]
    if horizontal:
        ax.barh(offsets, solid, height=bar_width, label=name, color=color)
    else:
        ax.bar(offsets, solid, width=bar_width, label=name, color=color)

    for index, value in enumerate(values):
        base = solid[index]
        if value != value or value - base <= 0:
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


def _draw_pie(ax, series, categories, style, font, base_pt) -> None:
    """A donut with a side legend carrying each slice's share.

    A bare pie in a wide content band left the left and right thirds of the
    slide empty and named its slices without ever saying what share they
    were - the one number a reader wants from a pie.
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
        category_axis.set_ticks(x)
        category_axis.set_ticklabels(categories, color=style.axis_color,
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
        ax.tick_params(**{"axis": "y" if horizontal else "x",
                          category_side: False, f"label{category_side}": False})

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
        ax.annotate(
            f"{_format_number(height)}{unit_suffix}",
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

    width = max(0.05, min(1.0, style.bar_thickness))
    running = 0.0
    bottoms, heights, colors = [], [], []
    for index, value in enumerate(values):
        if index in totals:
            bottoms.append(0.0)
            heights.append(value)
            colors.append(style.waterfall_total_color)
            running = value
        else:
            bottoms.append(running if value >= 0 else running + value)
            heights.append(abs(value))
            colors.append(
                style.waterfall_rise_color if value >= 0
                else style.waterfall_fall_color
            )
            running += value

    ax.bar(x, heights, bottom=bottoms, width=width, color=colors)
    _connect_waterfall(ax, x, bottoms, heights, values, totals, width, style)

    if style.label_mode != "none":
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
                         unit_suffix, x)
    bottom, top = ax.get_ylim()
    ax.set_ylim(bottom, top + (top - bottom) * 0.12)


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
    """
    for index in range(len(x) - 1):
        level = (bottoms[index] + heights[index]
                 if values[index] >= 0 or index in totals else bottoms[index])
        ax.plot(
            [x[index] + width / 2, x[index + 1] - width / 2], [level, level],
            color=style.axis_color, linewidth=0.8, linestyle=(0, (3, 3)),
            zorder=0,
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


def _draw_data_labels(
    ax, chart_type, series, x, style, font, base_pt, unit_suffix, chart_data,
) -> None:
    """Print each point's value next to it, where that stays readable."""
    if chart_data.get("showDataLabels") is False or style.label_mode == "none":
        return
    if chart_type == "scatter":
        return

    total_points = sum(len(s["values"]) for s in series)
    crowded = (total_points > MAX_LABELLED_POINTS * 2
               or len(x) > MAX_LABELLED_POINTS)
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
