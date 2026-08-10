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

import matplotlib
matplotlib.use("Agg")  # headless - this service has no display server
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.ticker import FuncFormatter

from app.renderer.table_style import resolve_chart_style
from app.observability.logging import get_logger

logger = get_logger(__name__)

SUPPORTED_CHART_TYPES = {"bar", "line", "pie", "scatter", "area"}

# Beyond this many points per series, per-point data labels collide into an
# unreadable smear - the chart is better read off the axis.
MAX_LABELLED_POINTS = 8
# A pie with more slices than this is unreadable; the tail is grouped.
MAX_PIE_SLICES = 7

_FONT_FALLBACKS = ("Arial", "Helvetica", "Liberation Sans", "DejaVu Sans")


def _resolve_font(preferred: str | None) -> str:
    """The first requested font actually installed on this host.

    The design system names a typeface for the deck (often Arial, which no
    Linux container has). matplotlib silently falls back with a warning per
    text object; picking a real family up front keeps the chart's type
    consistent with itself.
    """
    available = {f.name for f in font_manager.fontManager.ttflist}
    for candidate in (preferred, *_FONT_FALLBACKS):
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
            cleaned.append({"name": entry.get("name"), "values": values})
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


def render_chart_to_png(
    chart_data: dict,
    design_system: dict | None,
    width_in: float,
    height_in: float,
    dpi: int = 200,
    slide_title: str | None = None,
) -> bytes | None:
    """
    Render a ChartContent-shaped dict to a transparent-background PNG.

    Returns None (never raises) for malformed/empty chart data or a
    matplotlib failure, so a bad chart degrades to "no chart on this slide"
    rather than aborting the whole render.

    ``slide_title`` suppresses a chart title that merely restates the slide's
    heading, which is the usual case and reads as a stutter.
    """
    series = _clean_series(chart_data.get("series") or chart_data.get("data"))
    if not series:
        return None
    if width_in <= 0 or height_in <= 0:
        return None

    chart_type = (chart_data.get("chartType") or chart_data.get("chart_type") or "bar").lower()
    if chart_type not in SUPPORTED_CHART_TYPES:
        chart_type = "bar"

    categories = [str(c) for c in (chart_data.get("categories") or [])]
    style = resolve_chart_style(design_system)
    font = _resolve_font(style.font_name)

    unit_suffix = str(chart_data.get("valueSuffix") or "").strip()
    if not unit_suffix and str(chart_data.get("valueFormat") or "").lower() == "percent":
        unit_suffix = "%"

    # Point sizes are chosen against the band the chart is drawn into, so a
    # half-height chart does not render with type twice as large relative to
    # its own axes as a full-height one.
    base_pt = max(8.0, min(13.0, 3.6 * math.sqrt(width_in * height_in / 6.0) + 6.0))

    fig = None
    try:
        fig, ax = plt.subplots(figsize=(width_in, height_in), dpi=dpi)
        fig.patch.set_alpha(0.0)
        ax.set_facecolor("none")

        if chart_type == "pie":
            _draw_pie(ax, series, categories, style, font, base_pt)
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

        buf = io.BytesIO()
        fig.savefig(buf, format="png", transparent=True)
        return buf.getvalue()
    except Exception as e:
        logger.warning("chart_render_failed", chart_type=chart_type, error=str(e))
        return None
    finally:
        if fig is not None:
            plt.close(fig)


def _apply_title(fig, ax, chart_data, slide_title, style, font, base_pt) -> None:
    """Draw the chart's own title, unless the slide heading already says it."""
    title = str(chart_data.get("title") or "").strip()
    if not title:
        return
    if slide_title and _normalise(title) == _normalise(slide_title):
        return
    ax.set_title(
        title, color=style.axis_color, fontname=font,
        fontsize=base_pt * 1.15, fontweight="bold", loc="left", pad=base_pt,
    )


def _normalise(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _draw_pie(ax, series, categories, style, font, base_pt) -> None:
    """A donut with a side legend carrying each slice's share.

    A bare pie in a wide content band left the left and right thirds of the
    slide empty and named its slices without ever saying what share they
    were - the one number a reader wants from a pie.
    """
    values = [v for v in series[0]["values"] if v == v and v >= 0]
    labels = categories[:len(values)] or [f"Slice {i + 1}" for i in range(len(values))]
    labels = [str(l) for l in labels]
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


def _draw_cartesian(
    ax, chart_type, series, categories, chart_data, style, font, base_pt, unit_suffix,
) -> None:
    n_points = len(categories) if categories else max(len(s["values"]) for s in series)
    x = list(range(n_points))

    for i, s in enumerate(series):
        values = (s["values"] + [float("nan")] * n_points)[:n_points]
        name = str(s.get("name") or f"Series {i + 1}")
        color = style.color_sequence[i % len(style.color_sequence)]

        if chart_type == "line":
            ax.plot(x, values, label=name, color=color, linewidth=2.5, marker="o",
                    markersize=base_pt * 0.5)
        elif chart_type == "area":
            ax.plot(x, values, label=name, color=color, linewidth=2.0)
            ax.fill_between(x, values, color=color, alpha=0.22)
        elif chart_type == "scatter":
            ax.scatter(x, values, label=name, color=color, s=base_pt * 6)
        else:  # bar
            n_series = len(series)
            bar_width = 0.78 / max(1, n_series)
            offsets = [xi + i * bar_width - 0.39 + bar_width / 2 for xi in x]
            ax.bar(offsets, values, width=bar_width, label=name, color=color)

    if categories:
        ax.set_xticks(x)
        ax.set_xticklabels(categories, color=style.axis_color, fontname=font,
                           fontsize=base_pt)
    ax.tick_params(colors=style.axis_color, labelsize=base_pt)
    for label in ax.get_yticklabels():
        label.set_fontname(font)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(style.axis_color)
    ax.yaxis.grid(True, color=style.grid_color, linewidth=0.7)
    ax.set_axisbelow(True)
    ax.yaxis.set_major_formatter(_axis_formatter(unit_suffix))

    # A bar chart's length *is* its value, so a non-zero baseline misstates
    # the comparison. Line and scatter show change, where a zoomed axis is
    # legitimate, so they keep matplotlib's own range.
    if chart_type in ("bar", "area"):
        ax.set_ylim(bottom=min(0.0, ax.get_ylim()[0]))

    value_label = _value_axis_label(chart_data, series)
    if value_label:
        ax.set_ylabel(value_label, color=style.axis_color, fontname=font,
                      fontsize=base_pt, labelpad=base_pt * 0.6)

    category_label = str(
        chart_data.get("xAxisTitle") or chart_data.get("categoryAxisTitle") or ""
    ).strip()
    if category_label:
        ax.set_xlabel(category_label, color=style.axis_color, fontname=font,
                      fontsize=base_pt, labelpad=base_pt * 0.5)

    # A legend for one series would only repeat the value-axis label.
    if len(series) > 1:
        legend = ax.legend(frameon=False, fontsize=base_pt, labelcolor=style.axis_color,
                           loc="best")
        for text in legend.get_texts():
            text.set_fontname(font)

    _draw_data_labels(ax, chart_type, series, x, style, font, base_pt, unit_suffix,
                      chart_data)


def _draw_data_labels(
    ax, chart_type, series, x, style, font, base_pt, unit_suffix, chart_data,
) -> None:
    """Print each point's value next to it, where that stays readable."""
    if chart_data.get("showDataLabels") is False:
        return
    if chart_type == "scatter":
        return
    total_points = sum(len(s["values"]) for s in series)
    if total_points > MAX_LABELLED_POINTS * 2 or len(x) > MAX_LABELLED_POINTS:
        return

    n_series = len(series)
    bar_width = 0.78 / max(1, n_series)
    for i, s in enumerate(series):
        for xi, value in zip(x, s["values"]):
            if value != value:
                continue
            px = xi + i * bar_width - 0.39 + bar_width / 2 if chart_type == "bar" else xi
            ax.annotate(
                f"{_format_number(value)}{unit_suffix}",
                xy=(px, value),
                # Offset in points, not data units: a fixed typographic gap
                # above the marker whatever the series' magnitude.
                xytext=(0, base_pt * 0.45),
                textcoords="offset points",
                ha="center",
                va="bottom",
                color=style.axis_color,
                fontname=font,
                fontsize=base_pt * 0.85,
            )

    # Labels sit above the highest point, so the axis needs room for them.
    bottom, top = ax.get_ylim()
    ax.set_ylim(bottom, top + (top - bottom) * 0.12)
