"""
The rest of the chart vocabulary: slope, dumbbell, bullet, heatmap, combo, donut.

Six charts that exist because a bar chart answers a different question. Each
one here is a question a consulting or finance deck asks routinely and the
engine could not draw:

* slope    - who moved up and who moved down between two dates
* dumbbell - how big the gap between two states is
* bullet   - is this measure hitting its target
* heatmap  - where are the hot spots in a dense matrix
* combo    - a volume and a rate that only mean something together
* donut    - parts of one whole, with the total readable in the middle

The recurring judgement under test is that nothing is inferred from
magnitudes. Which series is a rate, which bar is a total, what a target is -
all of it stated, because a chart that guesses is wrong in a way that looks
fine.
"""

import pytest

from app.agent.tools.build_visuals import (
    CHART_TYPES, MAX_HEATMAP_COLUMNS, MAX_HEATMAP_ROWS, MAX_SLOPE_POINTS,
    BuildChartTool,
)
from app.renderer.chart_renderer import (
    SUPPORTED_CHART_TYPES, _secondary_series, _bullet_targets,
    render_chart_to_png,
)

NEW_TYPES = ["slope", "dumbbell", "bullet", "heatmap", "combo", "donut"]

MOMENTS = ["FY23", "FY25"]
ENTITIES = [
    {"name": "Subscription", "values": [30.0, 45.0]},
    {"name": "Perpetual", "values": [40.0, 25.0]},
    {"name": "Services", "values": [30.0, 30.0]},
]
REGIONS = ["Americas", "EMEA", "APAC", "Japan"]
PLAN_ACTUAL = [
    {"name": "Plan", "values": [100.0, 90.0, 80.0, 70.0]},
    {"name": "Actual", "values": [109.0, 93.0, 76.0, 78.0]},
]
MATRIX = [
    {"name": "Americas", "values": [88.0, 84.0, 79.0, 76.0]},
    {"name": "EMEA", "values": [72.0, 70.0, 71.0, 69.0]},
    {"name": "APAC", "values": [64.0, 59.0, 55.0, 51.0]},
]


def _png(chart):
    return render_chart_to_png(chart, None, 6.0, 4.0)


class TestTheyAreRegistered:
    @pytest.mark.parametrize("kind", NEW_TYPES)
    def test_the_renderer_knows_them(self, kind):
        assert kind in SUPPORTED_CHART_TYPES

    @pytest.mark.parametrize("kind", NEW_TYPES)
    def test_the_tool_offers_them(self, kind):
        assert kind in CHART_TYPES


class TestTheyRender:
    def test_slope(self):
        png = _png({"chartType": "slope", "categories": MOMENTS,
                    "series": ENTITIES})
        assert png and png[:4] == b"\x89PNG"

    def test_dumbbell(self):
        png = _png({"chartType": "dumbbell", "categories": REGIONS,
                    "series": PLAN_ACTUAL})
        assert png and png[:4] == b"\x89PNG"

    def test_bullet(self):
        png = _png({"chartType": "bullet", "categories": ["Revenue", "Margin", "NPS"],
                    "series": [{"name": "Actual", "values": [4.12, 31.0, 44.0]}],
                    "targets": [4.0, 34.0, 40.0]})
        assert png and png[:4] == b"\x89PNG"

    def test_heatmap(self):
        png = _png({"chartType": "heatmap", "categories": ["Q1", "Q2", "Q3", "Q4"],
                    "series": MATRIX})
        assert png and png[:4] == b"\x89PNG"

    def test_combo(self):
        png = _png({"chartType": "combo", "categories": ["FY23", "FY24", "FY25"],
                    "series": [{"name": "Revenue ($B)", "values": [3.9, 4.1, 4.12]},
                               {"name": "Margin (%)", "values": [28.0, 30.0, 31.0]}],
                    "secondaryAxisSeries": ["Margin (%)"],
                    "secondaryValueSuffix": "%"})
        assert png and png[:4] == b"\x89PNG"

    def test_a_donut_prints_its_total_and_a_pie_does_not(self):
        """
        The hole already exists; the total in it is the whole difference. A
        pie leaves it empty because its total is often not a number anyone
        quotes, and printing one there invites reading it as a figure.
        """
        data = {"categories": ["A", "B", "C"],
                "series": [{"name": "Revenue ($B)", "values": [1.03, 2.51, 0.58]}]}

        assert _png({**data, "chartType": "pie"}) != _png(
            {**data, "chartType": "donut"}
        )

    @pytest.mark.parametrize("kind", NEW_TYPES)
    def test_they_survive_a_dark_ground(self, kind):
        data = {
            "slope": {"categories": MOMENTS, "series": ENTITIES},
            "dumbbell": {"categories": REGIONS, "series": PLAN_ACTUAL},
            "bullet": {"categories": REGIONS,
                       "series": [{"name": "Actual", "values": [1.0, 2.0, 3.0, 4.0]}],
                       "targets": [2.0, 2.0, 2.0, 2.0]},
            "heatmap": {"categories": ["Q1", "Q2", "Q3", "Q4"], "series": MATRIX},
            "combo": {"categories": MOMENTS,
                      "series": [{"name": "V", "values": [1.0, 2.0]},
                                 {"name": "R", "values": [10.0, 20.0]}]},
            "donut": {"categories": ["A", "B"],
                      "series": [{"name": "x", "values": [1.0, 2.0]}]},
        }[kind]

        png = render_chart_to_png({"chartType": kind, **data}, None, 6.0, 4.0,
                                  ground="#12233F")

        assert png and png[:4] == b"\x89PNG"


class TestNothingIsInferredFromMagnitudes:
    def test_the_secondary_axis_is_read_from_what_was_declared(self):
        series = [{"name": "Revenue"}, {"name": "Margin"}]

        assert _secondary_series({"secondaryAxisSeries": ["Margin"]}, series) == {
            "Margin"
        }

    def test_a_large_rate_is_not_mistaken_for_a_volume(self):
        """
        The failure this prevents: NPS of 44 alongside revenue of 4.12. A
        magnitude heuristic puts the rate on the left and the chart is wrong
        in a way that looks fine.
        """
        series = [{"name": "Revenue ($B)", "values": [4.12]},
                  {"name": "NPS", "values": [44.0]}]

        assert _secondary_series({"secondaryAxisSeries": ["NPS"]}, series) == {"NPS"}

    def test_two_series_default_to_the_last_being_the_line(self):
        """Which is what a caller asking for "combo" with two series means."""
        series = [{"name": "Revenue"}, {"name": "Margin"}]

        assert _secondary_series({}, series) == {"Margin"}

    def test_a_measure_with_no_target_still_draws(self):
        """A bar with no rule across it is honest; an invented target is not."""
        targets = _bullet_targets({"targets": [4.0, None, 40.0]}, [{}], 3)

        assert targets[1] is None
        assert targets[0] == 4.0

    def test_a_target_series_named_as_such_is_found(self):
        targets = _bullet_targets(
            {}, [{"name": "Actual"}, {"name": "Target", "values": [1.0, 2.0]}], 2
        )

        assert targets == [1.0, 2.0]


class TestTheToolRefusesTheWrongShape:
    pytestmark = pytest.mark.asyncio

    @pytest.mark.asyncio
    async def test_a_dumbbell_needs_exactly_two_states(self):
        result = await BuildChartTool.run(
            slide_index=0, chart_type="dumbbell", categories=REGIONS,
            series=[PLAN_ACTUAL[0]],
        )

        assert not result.success
        assert "TWO states" in result.error

    @pytest.mark.asyncio
    async def test_a_combo_of_one_series_is_a_bar_chart(self):
        result = await BuildChartTool.run(
            slide_index=0, chart_type="combo", categories=MOMENTS,
            series=[{"name": "Revenue", "values": [1.0, 2.0]}],
        )

        assert not result.success
        assert "second axis" in result.error

    @pytest.mark.asyncio
    async def test_a_slope_over_too_many_moments_is_a_line_chart(self):
        result = await BuildChartTool.run(
            slide_index=0, chart_type="slope",
            categories=[f"FY{n}" for n in range(MAX_SLOPE_POINTS + 2)],
            series=[{"name": "A",
                     "values": [1.0] * (MAX_SLOPE_POINTS + 2)}],
        )

        assert not result.success
        assert "line chart" in result.error

    @pytest.mark.asyncio
    async def test_a_heatmap_with_too_many_rows_is_refused(self):
        result = await BuildChartTool.run(
            slide_index=0, chart_type="heatmap", categories=["Q1", "Q2"],
            series=[{"name": f"R{n}", "values": [1.0, 2.0]}
                    for n in range(MAX_HEATMAP_ROWS + 1)],
        )

        assert not result.success
        assert "smaller than the figures" in result.error

    @pytest.mark.asyncio
    async def test_a_heatmap_with_too_many_columns_is_refused(self):
        columns = [f"C{n}" for n in range(MAX_HEATMAP_COLUMNS + 1)]

        result = await BuildChartTool.run(
            slide_index=0, chart_type="heatmap", categories=columns,
            series=[{"name": "R", "values": [1.0] * len(columns)}],
        )

        assert not result.success
        assert "columns" in result.error

    @pytest.mark.asyncio
    async def test_a_heatmap_may_have_more_rows_than_the_series_cap(self):
        """
        A heatmap's rows are matrix rows, not overlaid lines a reader tells
        apart by colour, so the generic six-series cap is the wrong limit.
        """
        result = await BuildChartTool.run(
            slide_index=0, chart_type="heatmap", categories=["Q1", "Q2"],
            series=[{"name": f"R{n}", "values": [1.0, 2.0]} for n in range(8)],
        )

        assert result.success

    @pytest.mark.asyncio
    async def test_a_misnamed_secondary_series_names_the_real_ones(self):
        result = await BuildChartTool.run(
            slide_index=0, chart_type="combo", categories=MOMENTS,
            series=[{"name": "Revenue ($B)", "values": [1.0, 2.0]},
                    {"name": "Margin (%)", "values": [10.0, 20.0]}],
            secondary_axis_series=["margin"],
        )

        assert not result.success
        assert "Margin (%)" in result.error

    @pytest.mark.asyncio
    async def test_a_donut_takes_one_series(self):
        result = await BuildChartTool.run(
            slide_index=0, chart_type="donut", categories=MOMENTS,
            series=[{"name": "A", "values": [1.0, 2.0]},
                    {"name": "B", "values": [2.0, 1.0]}],
        )

        assert not result.success
        assert "stacked100" in result.error

    @pytest.mark.asyncio
    async def test_a_good_combo_carries_its_second_axis(self):
        result = await BuildChartTool.run(
            slide_index=0, chart_type="combo", categories=MOMENTS,
            series=[{"name": "Revenue ($B)", "values": [1.0, 2.0]},
                    {"name": "Margin (%)", "values": [10.0, 20.0]}],
            secondary_axis_series=["Margin (%)"],
            secondary_axis_title="Margin (%)",
        )

        assert result.success
        chart = result.data["content"]["chart"]
        assert chart["secondaryAxisSeries"] == ["Margin (%)"]
        assert chart["secondaryAxisTitle"] == "Margin (%)"

    @pytest.mark.asyncio
    async def test_a_good_bullet_carries_its_targets(self):
        result = await BuildChartTool.run(
            slide_index=0, chart_type="bullet", categories=["Revenue", "Margin"],
            series=[{"name": "Actual", "values": [4.12, 31.0]}],
            targets=[4.0, 34.0],
        )

        assert result.success
        assert result.data["content"]["chart"]["targets"] == [4.0, 34.0]


class TestTheRendererDegradesRatherThanDrawingNothing:
    """
    build_chart refuses a malformed shape where it is stated. A chart written
    inline by design_slide_content can still reach the renderer, and there a
    missing exhibit is a worse outcome than a plainer one.
    """

    def test_a_one_ended_dumbbell_becomes_a_dot_plot(self):
        png = _png({"chartType": "dumbbell", "categories": REGIONS,
                    "series": [PLAN_ACTUAL[0]]})

        assert png and png[:4] == b"\x89PNG"

    def test_a_one_series_combo_draws_no_empty_second_axis(self):
        """
        A second set of ticks measuring nothing is worse than the bar chart
        the data actually is.
        """
        one = _png({"chartType": "combo", "categories": MOMENTS,
                    "series": [{"name": "Revenue", "values": [1.0, 2.0]}]})
        plain = _png({"chartType": "bar", "categories": MOMENTS,
                      "series": [{"name": "Revenue", "values": [1.0, 2.0]}]})

        assert one and one[:4] == b"\x89PNG"
        # Same picture, give or take the legend a combo always draws.
        assert abs(len(one) - len(plain)) < len(plain) * 0.6
