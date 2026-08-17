"""
The bar family: stacked, stacked100 and waterfall.

Three charts that share a drawing and answer three different questions, none
of which a plain bar chart answers:

* ``stacked`` - what is this total made of?
* ``stacked100`` - what changed underneath a total that barely moved?
* ``waterfall`` - where did the movement between two totals come from?

The last is the one a finance deck reaches for most and had no way to draw.
"""

import pytest

from app.agent.tools.build_visuals import (
    CHART_TYPES, MAX_STACK_SERIES, BuildChartTool,
)
from app.renderer.chart_renderer import (
    MAX_STACK_SEGMENTS, SUPPORTED_CHART_TYPES, _total_indices,
    render_chart_to_png,
)

MIX = [
    {"name": "Perpetual", "values": [40.0, 33.0, 25.0]},
    {"name": "Subscription", "values": [30.0, 38.0, 45.0]},
    {"name": "Services", "values": [30.0, 29.0, 30.0]},
]
YEARS = ["FY23", "FY24", "FY25"]

BRIDGE = {
    "chartType": "waterfall",
    "categories": ["FY24", "Subs", "Services", "Licence", "FX", "FY25"],
    "series": [{"name": "US$ millions",
                "values": [4104.0, 72.0, 44.0, -42.0, -26.0, 4152.0]}],
}


def _png(chart):
    return render_chart_to_png(chart, None, 6.0, 4.0)


class TestTheyAreRegistered:
    @pytest.mark.parametrize("kind", ["stacked", "stacked100", "waterfall"])
    def test_the_renderer_knows_them(self, kind):
        assert kind in SUPPORTED_CHART_TYPES

    @pytest.mark.parametrize("kind", ["stacked", "stacked100", "waterfall"])
    def test_the_tool_offers_them(self, kind):
        assert kind in CHART_TYPES


class TestTheyRender:
    @pytest.mark.parametrize("kind", ["stacked", "stacked100"])
    def test_a_stack_produces_a_png(self, kind):
        png = _png({"chartType": kind, "categories": YEARS, "series": MIX})

        assert png and png[:4] == b"\x89PNG"

    def test_a_waterfall_produces_a_png(self):
        png = _png(BRIDGE)

        assert png and png[:4] == b"\x89PNG"

    def test_the_two_stacks_are_different_pictures(self):
        """stacked100 normalises the totals away; that has to be visible."""
        plain = _png({"chartType": "stacked", "categories": YEARS, "series": MIX})
        percent = _png({"chartType": "stacked100", "categories": YEARS,
                        "series": MIX})

        assert plain != percent

    def test_a_stack_survives_a_dark_ground(self):
        png = _png({"chartType": "stacked", "categories": YEARS, "series": MIX,
                    "ground": "#12233F"})

        assert png and png[:4] == b"\x89PNG"

    def test_a_stack_with_more_bands_than_readable_still_renders(self):
        """The tail is grouped, not dropped - a chart, not a failure."""
        many = [{"name": f"Band {n}", "values": [10.0, 10.0, 10.0]}
                for n in range(MAX_STACK_SEGMENTS + 4)]

        png = _png({"chartType": "stacked", "categories": YEARS, "series": many})

        assert png and png[:4] == b"\x89PNG"


class TestAWaterfallSaysWhichBarsAreTotals:
    def test_the_ends_are_totals_by_default(self):
        assert _total_indices({}, 6) == {0, 5}

    def test_a_declared_subtotal_is_honoured(self):
        """
        Guessing totals from the numbers draws a real subtotal floating, and
        a reader has no way to see that happened.
        """
        assert _total_indices({"totalIndices": [0, 3, 6]}, 7) == {0, 3, 6}

    def test_an_out_of_range_index_is_ignored_not_fatal(self):
        assert _total_indices({"totalIndices": [0, 99]}, 4) == {0}

    def test_junk_falls_back_to_the_ends(self):
        assert _total_indices({"totalIndices": ["x", None]}, 4) == {0, 3}

    def test_a_declared_subtotal_changes_the_drawing(self):
        chart = dict(BRIDGE)
        with_subtotal = {**chart, "totalIndices": [0, 3, 5]}

        assert _png(chart) != _png(with_subtotal)


class TestTheToolRefusesTheWrongShape:
    pytestmark = pytest.mark.asyncio

    @staticmethod
    async def _build(**kwargs):
        return await BuildChartTool.run(**{
            "slide_index": 0, "categories": YEARS,
            "series": MIX, **kwargs,
        })

    @pytest.mark.asyncio
    async def test_a_stack_of_one_is_a_bar_chart(self):
        result = await self._build(
            chart_type="stacked", series=[MIX[0]],
        )

        assert not result.success
        assert "MADE OF" in result.error

    @pytest.mark.asyncio
    async def test_too_many_bands_are_refused_where_they_can_be_changed(self):
        result = await self._build(
            chart_type="stacked",
            series=[{"name": f"B{n}", "values": [1.0, 1.0, 1.0]}
                    for n in range(MAX_STACK_SERIES + 1)],
        )

        assert not result.success
        assert "'Other'" in result.error

    @pytest.mark.asyncio
    async def test_a_waterfall_takes_one_series(self):
        result = await self._build(chart_type="waterfall", series=MIX)

        assert not result.success
        assert "ONE running total" in result.error

    @pytest.mark.asyncio
    async def test_a_two_bar_waterfall_is_refused(self):
        result = await self._build(
            chart_type="waterfall", categories=["FY24", "FY25"],
            series=[{"name": "US$m", "values": [100.0, 120.0]}],
        )

        assert not result.success
        assert "reads better as text" in result.error

    @pytest.mark.asyncio
    async def test_a_column_summing_to_zero_is_refused_for_stacked100(self):
        """
        An empty 100% bar reads as "this category is zero" rather than "we
        have no breakdown for it" - the same lie the empty-table check
        refuses.
        """
        result = await self._build(
            chart_type="stacked100",
            series=[{"name": "A", "values": [40.0, 0.0, 25.0]},
                    {"name": "B", "values": [60.0, 0.0, 75.0]}],
        )

        assert not result.success
        assert "'FY24'" in result.error

    @pytest.mark.asyncio
    async def test_a_good_bridge_is_built(self):
        result = await self._build(
            chart_type="waterfall",
            categories=BRIDGE["categories"],
            series=BRIDGE["series"],
            total_indices=[0, 5],
        )

        assert result.success
        chart = result.data["content"]["chart"]
        assert chart["chartType"] == "waterfall"
        assert chart["totalIndices"] == [0, 5]

    @pytest.mark.asyncio
    async def test_a_good_stack_is_built(self):
        result = await self._build(chart_type="stacked100")

        assert result.success
        assert result.data["content"]["chart"]["chartType"] == "stacked100"
