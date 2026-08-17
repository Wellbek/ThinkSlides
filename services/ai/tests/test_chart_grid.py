"""
Small multiples: the same shape per segment, on one shared scale.

The exhibit that answers a question no single chart can - does this pattern
hold everywhere, or is one segment carrying it? Four regional trends overlaid
on one axes is a tangle; the same four as a row of panels is readable at a
glance.

The shared scale is the whole thing, and these tests exist mostly to pin it
down. Panels scaled independently are the classic small-multiple lie: four
charts that look alike and are not comparable, where the one with the smallest
numbers gets the steepest-looking line.
"""

import pytest

from app.agent.tools.build_visuals import (
    BUILD_VISUAL_TOOLS, MAX_PANELS, MIN_PANELS, BuildChartGridTool,
)
from app.renderer.chart_renderer import SUPPORTED_CHART_TYPES, render_chart_to_png

QUARTERS = ["Q1", "Q2", "Q3", "Q4"]
REGIONS = [
    {"name": "Americas", "values": [88.0, 91.0, 95.0, 99.0]},
    {"name": "EMEA", "values": [72.0, 73.0, 75.0, 77.0]},
    {"name": "APAC", "values": [64.0, 61.0, 58.0, 55.0]},
    {"name": "Japan", "values": [40.0, 44.0, 47.0, 52.0]},
]


async def _grid(**kwargs):
    return await BuildChartGridTool.run(**{
        "slide_index": 0, "categories": QUARTERS, "panels": REGIONS, **kwargs,
    })


class TestItIsRegistered:
    def test_the_renderer_knows_the_shape(self):
        """
        Asserted through resolve_chart_type, not against the set. Membership
        was true while the name was registered in camelCase and the renderer
        lowercased what it was given, so every grid silently drew a grouped
        bar chart - see test_chart_defects.
        """
        from app.renderer.chart_renderer import resolve_chart_type

        assert resolve_chart_type("smallMultiples") == ("smallmultiples", True)
        assert "smallmultiples" in SUPPORTED_CHART_TYPES

    def test_the_tool_is_one_of_the_visual_builders(self):
        assert "build_chart_grid" in BUILD_VISUAL_TOOLS

    def test_it_is_offered_while_a_slide_is_drafted(self):
        from app.agent.pipeline import SLIDE_TOOLS

        assert "build_chart_grid" in SLIDE_TOOLS


class TestTheScaleIsShared:
    """The property that makes the grid worth having, enforced not offered."""

    @staticmethod
    def _axes(series, panel_type="line"):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        from app.renderer.chart_renderer import _render_small_multiples
        from app.renderer.table_style import resolve_chart_style

        captured = []
        real = plt.subplots

        def spy(*args, **kwargs):
            fig, axes = real(*args, **kwargs)
            captured.append(axes)
            return fig, axes

        plt.subplots = spy
        try:
            _render_small_multiples(
                {"chartType": "smallMultiples", "panelType": panel_type},
                series, QUARTERS, resolve_chart_style(None), "DejaVu Sans",
                "", 7.0, 3.2, 100, None,
            )
        finally:
            plt.subplots = real
        axes = captured[0]
        return [a for row in axes for a in row][:len(series)]

    def test_every_panel_gets_the_same_y_limits(self):
        """
        A panel of 55-99 and a panel of 2-4 must share a domain. Autoscaled,
        the second would show a dramatic climb between two tiny numbers.
        """
        lopsided = [
            {"name": "Big", "values": [88.0, 91.0, 95.0, 99.0]},
            {"name": "Small", "values": [2.0, 3.0, 3.5, 4.0]},
        ]

        limits = {tuple(round(v, 6) for v in ax.get_ylim())
                  for ax in self._axes(lopsided)}

        assert len(limits) == 1, f"panels were scaled independently: {limits}"

    def test_a_bar_grid_starts_every_panel_at_zero(self):
        """A bar's length is its value, in a panel as much as in a chart."""
        axes = self._axes(REGIONS[:2], panel_type="bar")

        for ax in axes:
            assert ax.get_ylim()[0] <= 0.0


class TestItRenders:
    def _chart(self, **kwargs):
        return {"chartType": "smallMultiples", "categories": QUARTERS,
                "series": REGIONS, **kwargs}

    @pytest.mark.parametrize("panel_type", ["line", "bar", "area"])
    def test_each_panel_shape_produces_a_png(self, panel_type):
        png = render_chart_to_png(self._chart(panelType=panel_type), None, 7.0, 3.2)

        assert png and png[:4] == b"\x89PNG"

    def test_an_odd_panel_count_still_renders(self):
        """Five panels means one empty cell, which must be a hole not a panel."""
        png = render_chart_to_png(
            {"chartType": "smallMultiples", "categories": QUARTERS,
             "series": [*REGIONS, {"name": "LatAm", "values": [1.0, 2.0, 3.0, 4.0]}]},
            None, 7.0, 3.2,
        )

        assert png and png[:4] == b"\x89PNG"

    def test_it_survives_a_dark_ground(self):
        png = render_chart_to_png(self._chart(), None, 7.0, 3.2, ground="#12233F")

        assert png and png[:4] == b"\x89PNG"

    def test_a_grid_of_nothing_renders_nothing_rather_than_raising(self):
        assert render_chart_to_png(
            {"chartType": "smallMultiples", "categories": QUARTERS, "series": []},
            None, 7.0, 3.2,
        ) is None


class TestTheTool:
    pytestmark = pytest.mark.asyncio

    @pytest.mark.asyncio
    async def test_a_well_formed_grid_becomes_a_chart_block(self):
        result = await _grid()

        assert result.success
        chart = result.data["content"]["chart"]
        assert len(chart["series"]) == 4
        # The block the tool emits has to be one the renderer resolves, which
        # is the thing that was broken.
        from app.renderer.chart_renderer import resolve_chart_type

        assert resolve_chart_type(chart["chartType"]) == ("smallmultiples", True)

    @pytest.mark.asyncio
    async def test_it_binds_to_a_region_like_any_other_exhibit(self):
        result = await _grid(region_id="exhibitBottomRight", slot="regions")

        assert result.data["content"]["regionId"] == "exhibitBottomRight"
        assert result.data["content"]["slot"] == "regions"

    @pytest.mark.asyncio
    async def test_a_grid_below_the_minimum_is_a_chart(self):
        result = await _grid(panels=REGIONS[:MIN_PANELS - 1])

        assert not result.success
        assert "build_chart" in result.error

    @pytest.mark.asyncio
    async def test_too_many_panels_are_refused(self):
        result = await _grid(panels=[
            {"name": f"Segment {n}", "values": [1.0, 2.0, 3.0, 4.0]}
            for n in range(MAX_PANELS + 1)
        ])

        assert not result.success
        assert "carry the finding" in result.error

    @pytest.mark.asyncio
    async def test_a_panel_measured_over_a_different_span_is_refused(self):
        """
        Not comparable with the others, which is the whole point of the grid.
        """
        result = await _grid(panels=[
            REGIONS[0], {"name": "EMEA", "values": [72.0, 73.0]},
        ])

        assert not result.success
        assert "not comparable" in result.error

    @pytest.mark.asyncio
    async def test_a_panel_without_a_name_is_refused(self):
        result = await _grid(panels=[
            {"values": [1.0, 2.0, 3.0, 4.0]}, REGIONS[1],
        ])

        assert not result.success
        assert "which segment" in result.error

    @pytest.mark.asyncio
    async def test_a_unit_inside_a_value_is_refused(self):
        result = await _grid(panels=[
            {"name": "A", "values": ["88%", "91%", "95%", "99%"]}, REGIONS[1],
        ])

        assert not result.success
        assert "value_label" in result.error

    @pytest.mark.asyncio
    async def test_empty_categories_are_refused(self):
        result = await _grid(categories=[])

        assert not result.success
        assert "comparable" in result.error

    @pytest.mark.asyncio
    async def test_an_unknown_panel_type_falls_back_rather_than_failing(self):
        result = await _grid(panel_type="sunburst")

        assert result.success
        assert result.data["content"]["chart"]["panelType"] == "line"

    @pytest.mark.asyncio
    async def test_json_encoded_panels_are_accepted(self):
        result = await BuildChartGridTool.run(
            slide_index=0, categories='["Q1", "Q2"]',
            panels='[{"name": "A", "values": [1, 2]}, {"name": "B", "values": [3, 4]}]',
        )

        assert result.success

    @pytest.mark.asyncio
    async def test_the_shared_label_is_carried_once(self):
        result = await _grid(value_label="Index (2020 = 100)")

        assert result.data["content"]["chart"]["yAxisTitle"] == "Index (2020 = 100)"

    @pytest.mark.asyncio
    async def test_it_reaches_the_slide_through_the_loop(self):
        from app.agent.loop import WorkingState
        from app.agent.tools._binding import slot_key

        state = WorkingState()
        result = await _grid(region_id="exhibitBottomRight", slot="regions")
        block = result.data["content"]
        slots = state.prepared_visuals.setdefault(0, [])
        slots[:] = [s for s in slots if slot_key(s) != slot_key(block)]
        slots.append(block)

        state.resolve_and_add_slide(
            {"slideIndex": 0, "layoutId": "content", "contents": []}
        )

        contents = state.accumulated_slides[0]["contents"]
        assert contents[0]["chart"]["chartType"] == "smallMultiples"
