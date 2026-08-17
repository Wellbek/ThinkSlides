"""
Horizontal bars: the orientation that was referenced and never registered.

``chart_renderer.py`` carried a styling branch reading
``chart_type == "barh"``, and "barh" appeared nowhere else in the repository -
not in SUPPORTED_CHART_TYPES, not in the tool's enum. The branch could never
fire.

It is not a nice-to-have. A vertical bar chart labelled "Enterprise software
and services" either rotates that label forty-five degrees or truncates it,
and both read badly on a slide. Which of the two matplotlib picks is a detail;
that the chart is worse either way is not.
"""

import pytest

from app.agent.tools.build_visuals import (
    CHART_TYPES, HORIZONTAL_LABEL_BUDGET, BuildChartTool,
)
from app.renderer.chart_renderer import SUPPORTED_CHART_TYPES, render_chart_to_png

LONG_CATEGORIES = [
    "Enterprise software and services",
    "Manufacturing and industrials",
    "Financial services and insurance",
    "Healthcare and life sciences",
]
SHORT_CATEGORIES = ["2021", "2022", "2023", "2024"]


def _chart(categories, chart_type="bar"):
    return {
        "chartType": chart_type,
        "categories": categories,
        "series": [{"name": "Adoption (%)", "values": [61.0, 44.0, 38.0, 25.0]}],
    }


class TestItIsActuallyRegistered:
    def test_the_renderer_knows_it(self):
        assert "barh" in SUPPORTED_CHART_TYPES

    def test_the_tool_offers_it(self):
        assert "barh" in CHART_TYPES


class TestTheToolTurnsBarsOnTheirSide:
    pytestmark = pytest.mark.asyncio

    @pytest.mark.asyncio
    async def test_long_labels_switch_the_orientation(self):
        result = await BuildChartTool.run(
            slide_index=0, chart_type="bar", categories=LONG_CATEGORIES,
            series=[{"name": "Adoption (%)", "values": [61, 44, 38, 25]}],
        )

        assert result.success
        assert result.data["content"]["chart"]["chartType"] == "barh"

    @pytest.mark.asyncio
    async def test_it_says_which_orientation_it_chose_and_why(self):
        """A silent substitution is one the caller cannot reason about."""
        result = await BuildChartTool.run(
            slide_index=0, chart_type="bar", categories=LONG_CATEGORIES,
            series=[{"name": "Adoption (%)", "values": [61, 44, 38, 25]}],
        )

        assert "horizontal bars" in result.data["summary"]
        assert "Enterprise software and services" in result.data["summary"]

    @pytest.mark.asyncio
    async def test_short_labels_stay_vertical(self):
        result = await BuildChartTool.run(
            slide_index=0, chart_type="bar", categories=SHORT_CATEGORIES,
            series=[{"name": "Revenue ($B)", "values": [1, 2, 3, 4]}],
        )

        assert result.data["content"]["chart"]["chartType"] == "bar"
        assert "horizontal" not in result.data["summary"]

    @pytest.mark.asyncio
    async def test_many_short_labels_stay_vertical(self):
        """The budget is total label length, not category count."""
        categories = [str(2000 + n) for n in range(12)]

        result = await BuildChartTool.run(
            slide_index=0, chart_type="bar", categories=categories,
            series=[{"name": "n", "values": list(range(12))}],
        )

        assert sum(len(c) for c in categories) <= HORIZONTAL_LABEL_BUDGET
        assert result.data["content"]["chart"]["chartType"] == "bar"

    @pytest.mark.asyncio
    async def test_an_explicit_choice_is_left_alone(self):
        result = await BuildChartTool.run(
            slide_index=0, chart_type="barh", categories=SHORT_CATEGORIES,
            series=[{"name": "Revenue ($B)", "values": [1, 2, 3, 4]}],
        )

        assert result.data["content"]["chart"]["chartType"] == "barh"

    @pytest.mark.asyncio
    async def test_a_line_chart_is_never_reoriented(self):
        result = await BuildChartTool.run(
            slide_index=0, chart_type="line", categories=LONG_CATEGORIES,
            series=[{"name": "Adoption (%)", "values": [61, 44, 38, 25]}],
        )

        assert result.data["content"]["chart"]["chartType"] == "line"


class TestItRenders:
    def test_a_horizontal_bar_chart_produces_a_png(self):
        png = render_chart_to_png(_chart(LONG_CATEGORIES, "barh"), None, 6.0, 4.0)

        assert png and png[:4] == b"\x89PNG"

    def test_it_differs_from_the_vertical_rendering(self):
        """Same data, genuinely different picture."""
        vertical = render_chart_to_png(_chart(SHORT_CATEGORIES, "bar"), None, 6.0, 4.0)
        horizontal = render_chart_to_png(_chart(SHORT_CATEGORIES, "barh"), None, 6.0, 4.0)

        assert vertical != horizontal

    def test_the_first_category_is_drawn_at_the_top(self):
        """
        Reading order runs down the page. matplotlib counts y upwards, which
        would otherwise print a ranked list upside down.
        """
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        from app.renderer.chart_renderer import _draw_cartesian
        from app.renderer.table_style import resolve_chart_style

        fig, ax = plt.subplots()
        try:
            _draw_cartesian(
                ax, "barh",
                [{"name": "Adoption (%)", "values": [61.0, 44.0, 38.0, 25.0]}],
                LONG_CATEGORIES, {}, resolve_chart_style(None), "DejaVu Sans",
                10.0, "",
            )
            bottom, top = ax.get_ylim()
        finally:
            plt.close(fig)

        assert bottom > top, "the y axis was not inverted, so rank 1 is at the bottom"

    def test_it_survives_a_dark_ground(self):
        png = render_chart_to_png(
            _chart(LONG_CATEGORIES, "barh"), None, 6.0, 4.0, ground="#12233F"
        )

        assert png and png[:4] == b"\x89PNG"

    def test_a_projection_still_renders_as_an_outline(self):
        """The forecast marking is orientation-independent, and must stay so."""
        chart = _chart(LONG_CATEGORIES, "barh")
        chart["series"][0]["forecastFrom"] = 3

        png = render_chart_to_png(chart, None, 6.0, 4.0)

        assert png and png != render_chart_to_png(
            _chart(LONG_CATEGORIES, "barh"), None, 6.0, 4.0
        )
