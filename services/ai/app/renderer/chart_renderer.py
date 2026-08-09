"""
Renders ChartContent slide data to a PNG via matplotlib, styled from the
run's design system (table_style.resolve_chart_style).

Charts are rendered as images rather than native OOXML <c:chart> parts:
matplotlib gives full control over brand colors/fonts, and the result
renders identically in LibreOffice and PowerPoint since it's just a
picture - native chart parts are comparatively fragile and inconsistently
rendered across chart engines, which matters more here than editability.
"""

from __future__ import annotations
import io

import matplotlib
matplotlib.use("Agg")  # headless - this service has no display server
import matplotlib.pyplot as plt

from app.renderer.table_style import resolve_chart_style
from app.observability.logging import get_logger

logger = get_logger(__name__)

SUPPORTED_CHART_TYPES = {"bar", "line", "pie", "scatter"}


def render_chart_to_png(
    chart_data: dict,
    design_system: dict | None,
    width_in: float,
    height_in: float,
    dpi: int = 200,
) -> bytes | None:
    """
    Render a ChartContent-shaped dict ({chartType, categories, series:
    [{name, values}]}) to a transparent-background PNG. Returns None (never
    raises) for malformed/empty chart data or a matplotlib failure, so a bad
    chart degrades to "no chart on this slide" rather than aborting the
    whole render.
    """
    raw_series = chart_data.get("series") or chart_data.get("data") or []
    # Drop any non-dict entries outright rather than only checking the first
    # one - a mix of one valid dict and one malformed entry would otherwise
    # pass this guard and crash later on `.get` against a non-dict.
    series = [s for s in raw_series if isinstance(s, dict) and s.get("values")]
    if not series:
        return None
    if width_in <= 0 or height_in <= 0:
        return None

    chart_type = (chart_data.get("chartType") or chart_data.get("chart_type") or "bar").lower()
    if chart_type not in SUPPORTED_CHART_TYPES:
        chart_type = "bar"

    categories = chart_data.get("categories") or []
    style = resolve_chart_style(design_system)

    fig = None
    try:
        fig, ax = plt.subplots(figsize=(width_in, height_in), dpi=dpi)
        fig.patch.set_alpha(0.0)
        ax.set_facecolor("none")

        if chart_type == "pie":
            values = series[0].get("values", [])
            labels = categories or [f"Slice {i + 1}" for i in range(len(values))]
            ax.pie(
                values,
                labels=labels,
                colors=style.color_sequence,
                textprops={"fontsize": 10, "color": style.axis_color},
            )
            ax.axis("equal")
        else:
            n_points = len(categories) if categories else len(series[0].get("values", []))
            x = list(range(n_points))

            for i, s in enumerate(series):
                values = s.get("values", [])
                name = s.get("name", f"Series {i + 1}")
                color = style.color_sequence[i % len(style.color_sequence)]

                if chart_type == "line":
                    ax.plot(x, values, label=name, color=color, linewidth=2.5, marker="o")
                elif chart_type == "scatter":
                    ax.scatter(x, values, label=name, color=color, s=60)
                else:  # bar
                    n_series = len(series)
                    bar_width = 0.8 / max(1, n_series)
                    offsets = [xi + i * bar_width - 0.4 + bar_width / 2 for xi in x]
                    ax.bar(offsets, values, width=bar_width, label=name, color=color)

            if categories:
                ax.set_xticks(x)
                ax.set_xticklabels(categories, color=style.axis_color)
            ax.tick_params(colors=style.axis_color)
            for side in ("top", "right"):
                ax.spines[side].set_visible(False)
            for side in ("left", "bottom"):
                ax.spines[side].set_color(style.axis_color)
            ax.yaxis.grid(True, color=style.grid_color, linewidth=0.7)
            ax.set_axisbelow(True)
            if len(series) > 1:
                ax.legend(frameon=False, fontsize=9, labelcolor=style.axis_color)

        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight", transparent=True)
        return buf.getvalue()
    except Exception as e:
        logger.warning("chart_render_failed", chart_type=chart_type, error=str(e))
        return None
    finally:
        if fig is not None:
            plt.close(fig)
