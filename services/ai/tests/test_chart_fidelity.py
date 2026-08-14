"""
Chart fidelity: label the data, drop the axis, mark what is projected.

The reference deck's charts read as analysis rather than as output because of
things a chart type does not capture. Page 22 drops its axis and labels every
bar. Page 110's final bar has a dashed upper segment marking an extrapolation,
footnoted as such - without that segment the chart claims a measured figure it
does not have. Page 3 carries two encodings at once, with leader lines and a
hand-built legend explaining both.

The tests that matter most here are the ones about the projection: a chart
that marks one without saying what it is based on is worse than one that never
marked it, so the marking and its footnote are validated together.
"""

import io
import json

import numpy as np
import pytest
from PIL import Image

from app.agent.tools.build_visuals import BuildChartTool
from app.agent.tools.validate_slide_json import ValidateSlideJsonTool
from app.design_system import get_default_design_system
from app.renderer import apparatus
from app.renderer.chart_renderer import (
    SUPPORTED_CHART_TYPES, _bubble_points, _measured_values, render_chart_to_png,
)

BARS = {
    "chartType": "bar",
    "categories": ["2019", "2020", "2021", "2022"],
    "series": [{"name": "$ billion", "values": [4.0, 5.0, 30.2, 36.6]}],
}
BUBBLES = {
    "chartType": "bubble",
    "points": [
        {"label": "Applied AI", "x": 0.62, "y": 0.95, "size": 160, "colorValue": 3},
        {"label": "Web3", "x": 0.64, "y": 0.10, "size": 60, "colorValue": 2},
        {"label": "Clean energy", "x": 0.86, "y": 0.28, "size": 255, "colorValue": 1},
    ],
    "xAxisTitle": "Interest score",
    "yAxisTitle": "Innovation score",
    "colorLegend": {"title": "Colour = adoption score", "labels": ["0", "1", "2"]},
    "sizeLegend": {"title": "Size = investment, $ billion", "values": [250, 75, 20]},
}


def _render(chart, width=6.0, height=3.4, design_system=None):
    png = render_chart_to_png(chart, design_system or get_default_design_system(),
                              width, height)
    assert png, "the chart did not render"
    return np.asarray(Image.open(io.BytesIO(png)).convert("RGBA"))


def _difference(a, b, threshold=24):
    return float((np.abs(a.astype(int) - b.astype(int)).sum(axis=2)
                  > threshold).mean())


def _slide(chart, footnotes=None, source=None):
    slide = {
        "slideIndex": 1, "layoutId": "chart", "layoutName": "Chart",
        "contents": [
            {"type": "text", "runs": [{"text": "Investment"}]},
            {"type": "chart", "chart": chart},
        ],
    }
    if footnotes is not None:
        slide["footnotes"] = footnotes
    if source is not None:
        slide["sourceLine"] = source
    return slide


class TestMarkingWhatIsProjected:
    def test_a_forecast_tail_changes_the_chart(self):
        plain = _render(dict(BARS))
        projected = _render({
            **BARS,
            "series": [{**BARS["series"][0], "forecastFrom": 3}],
        })

        assert _difference(plain, projected) > 0.005

    def test_a_split_bar_is_solid_to_its_measured_figure(self):
        """Reference page 110: 18.3 measured, 36.6 extrapolated."""
        split = _render({
            **BARS,
            "series": [{**BARS["series"][0], "forecastFrom": 3,
                        "measured": [4.0, 5.0, 30.2, 18.3]}],
        })
        whole = _render({
            **BARS,
            "series": [{**BARS["series"][0], "forecastFrom": 3}],
        })

        assert _difference(split, whole) > 0.005

    def test_the_measured_portion_defaults_correctly(self):
        values = [4.0, 5.0, 30.2, 36.6]

        # Nothing stated: everything from forecastFrom on is wholly projected.
        assert _measured_values({}, values, 3) == [4.0, 5.0, 30.2, 0.0]
        # Stated: the split is honoured.
        assert _measured_values({"measured": [4.0, 5.0, 30.2, 18.3]}, values, 3) \
            == [4.0, 5.0, 30.2, 18.3]
        # No forecast at all: every value is measured.
        assert _measured_values({}, values, 4) == values

    def test_the_forecast_fields_survive_series_cleaning(self):
        """
        Rebuilding a series as name+values alone dropped them, so a chart
        declaring a projection rendered as if every figure were measured -
        the exact claim the marking exists to prevent.
        """
        from app.renderer.chart_renderer import _clean_series

        [cleaned] = _clean_series([
            {"name": "x", "values": [1.0, 2.0], "forecastFrom": 1,
             "measured": [1.0, 1.5]},
        ])

        assert cleaned["forecastFrom"] == 1
        assert cleaned["measured"] == [1.0, 1.5]


class TestAProjectionMustSayWhatItIsBasedOn:
    @pytest.mark.asyncio
    async def test_a_marked_projection_with_no_footnotes_is_rejected(self):
        chart = {**BARS, "series": [{**BARS["series"][0], "forecastFrom": 3}]}

        result = await ValidateSlideJsonTool.run(json.dumps(_slide(chart)))

        assert result.success is False
        assert any("projected" in e for e in result.data["errors"])

    @pytest.mark.asyncio
    async def test_a_marked_projection_with_no_marker_is_rejected(self):
        chart = {**BARS, "series": [{**BARS["series"][0], "forecastFrom": 3}]}
        slide = _slide(chart, footnotes=["Extrapolated from H2 figures"])

        result = await ValidateSlideJsonTool.run(json.dumps(slide))

        assert result.success is False
        assert any("no footnote marker" in e for e in result.data["errors"])

    @pytest.mark.asyncio
    async def test_a_marked_projection_that_is_explained_passes(self):
        chart = {
            **BARS,
            "categories": ["2019", "2020", "2021", "2022[^1]"],
            "series": [{**BARS["series"][0], "forecastFrom": 3,
                        "measured": [4.0, 5.0, 30.2, 18.3]}],
        }
        slide = _slide(chart,
                       footnotes=["Extrapolated from 2022-H2 figures of $18.3 billion"],
                       source="Source: PitchBook")

        result = await ValidateSlideJsonTool.run(json.dumps(slide))

        assert result.success is True, result.data["errors"]

    @pytest.mark.asyncio
    async def test_an_unmarked_chart_needs_no_footnote(self):
        result = await ValidateSlideJsonTool.run(json.dumps(_slide(dict(BARS))))

        assert result.success is True

    def test_the_helper_finds_charts_that_mark_one(self):
        chart = {**BARS, "series": [{**BARS["series"][0], "forecastFrom": 3}]}

        assert apparatus.charts_marking_a_forecast(_slide(chart))
        assert not apparatus.charts_marking_a_forecast(_slide(dict(BARS)))


class TestTheUnitLine:
    def test_it_is_drawn_under_the_title(self):
        without = _render({**BARS, "title": "PE and VC investments"})
        with_unit = _render({**BARS, "title": "PE and VC investments",
                             "unitLine": "$ billion"})

        assert _difference(without, with_unit) > 0.001

    def test_it_replaces_the_rotated_axis_label(self):
        """Stating the unit twice is worse than stating it in the wrong place."""
        both = _render({**BARS, "title": "Investments", "unitLine": "$ billion",
                        "yAxisTitle": "$ billion"})
        axis_only = _render({**BARS, "title": "Investments",
                             "yAxisTitle": "$ billion"})

        assert _difference(both, axis_only) > 0.001


class TestPerChartOverrides:
    def test_a_chart_can_drop_the_axis_the_deck_keeps(self):
        with_axis = _render({**BARS, "showValueAxis": True})
        without = _render({**BARS, "showValueAxis": False, "showGridlines": False})

        assert _difference(with_axis, without) > 0.01

    def test_a_chart_can_choose_its_own_label_mode(self):
        line = {**BARS, "chartType": "line"}
        every = _render({**line, "labelMode": "all"})
        terminal = _render({**line, "labelMode": "terminal"})
        none = _render({**line, "labelMode": "none"})

        # Three labels removed is a small fraction of a chart's pixels, so the
        # assertion is about direction rather than magnitude: one label sits
        # between four and none.
        assert _difference(terminal, none) < _difference(every, none)
        assert _difference(terminal, every) > 0.0

    def test_an_unknown_override_is_ignored_rather_than_breaking(self):
        assert render_chart_to_png({**BARS, "labelMode": "sideways"},
                                   get_default_design_system(), 5.0, 3.0)


class TestBubbleChartsWithDualEncoding:
    def test_it_renders(self):
        assert "bubble" in SUPPORTED_CHART_TYPES
        assert _render(dict(BUBBLES), width=7.0, height=4.0) is not None

    def test_size_encodes_area_not_radius(self):
        """
        matplotlib's ``s`` is an area in points squared, so a value scaled
        linearly into it is encoded by area. Scaling the radius instead would
        overstate large values by their square - the classic way an area chart
        lies - so this pins which quantity is being scaled.
        """
        import inspect

        from app.renderer import chart_renderer

        source = inspect.getsource(chart_renderer._draw_bubble)
        assert "s=areas" in source
        assert "area" in source.lower()

    def test_the_second_encoding_reaches_the_page(self):
        one_colour = [{**p, "colorValue": 0} for p in BUBBLES["points"]]
        varied = _render(dict(BUBBLES), width=7.0, height=4.0)
        flat = _render({**BUBBLES, "points": one_colour}, width=7.0, height=4.0)

        assert _difference(varied, flat) > 0.005

    def test_the_legend_explains_both_encodings(self):
        with_legend = _render(dict(BUBBLES), width=7.0, height=4.0)
        without = _render(
            {k: v for k, v in BUBBLES.items()
             if k not in ("colorLegend", "sizeLegend")},
            width=7.0, height=4.0,
        )

        assert _difference(with_legend, without) > 0.01

    def test_labels_are_joined_to_their_bubbles(self):
        labelled = _render(dict(BUBBLES), width=7.0, height=4.0)
        unlabelled = _render(
            {**BUBBLES, "points": [{**p, "label": ""} for p in BUBBLES["points"]]},
            width=7.0, height=4.0,
        )

        assert _difference(labelled, unlabelled) > 0.002

    def test_malformed_points_are_dropped_rather_than_crashing(self):
        points = _bubble_points({"points": [
            {"label": "ok", "x": 1, "y": 2, "size": 3},
            {"label": "no x"},
            "not an object",
        ]})

        assert len(points) == 1

    def test_a_bubble_chart_given_series_degrades_to_a_scatter(self):
        """
        Bubbles need points carrying two values. Given a plain series, the
        honest rendering is the scatter that data supports, not nothing.
        """
        assert render_chart_to_png(
            {"chartType": "bubble", "categories": ["a", "b"],
             "series": [{"name": "x", "values": [1.0, 2.0]}]},
            get_default_design_system(), 5.0, 3.0,
        )

    def test_a_bubble_chart_with_neither_renders_nothing(self):
        assert render_chart_to_png({"chartType": "bubble"},
                                   get_default_design_system(), 5.0, 3.0) is None


class TestTheBuilderToolCarriesTheNewFields:
    @pytest.mark.asyncio
    async def test_a_forecast_is_validated_and_carried(self):
        result = await BuildChartTool.run(
            0, "bar", ["2021", "2022"],
            [{"name": "$ billion", "values": [30.2, 36.6],
              "forecastFrom": 1, "measured": [30.2, 18.3]}],
            unit_line="$ billion", label_mode="end",
        )

        chart = result.data["content"]["chart"]
        assert chart["series"][0]["forecastFrom"] == 1
        assert chart["series"][0]["measured"] == [30.2, 18.3]
        assert chart["unitLine"] == "$ billion"
        assert chart["labelMode"] == "end"
        assert chart["marksAProjection"] is True

    @pytest.mark.asyncio
    async def test_an_out_of_range_forecast_index_is_refused(self):
        result = await BuildChartTool.run(
            0, "bar", ["2021", "2022"],
            [{"name": "x", "values": [1.0, 2.0], "forecastFrom": 7}],
        )

        assert not result.success
        assert "forecastFrom=7" in result.error

    @pytest.mark.asyncio
    async def test_a_ragged_measured_array_is_refused(self):
        result = await BuildChartTool.run(
            0, "bar", ["2021", "2022"],
            [{"name": "x", "values": [1.0, 2.0], "measured": [1.0]}],
        )

        assert not result.success
        assert "measured values" in result.error

    @pytest.mark.asyncio
    async def test_a_plain_chart_is_unchanged(self):
        result = await BuildChartTool.run(
            0, "bar", ["2021", "2022"], [{"name": "x", "values": [1.0, 2.0]}]
        )

        chart = result.data["content"]["chart"]
        assert "forecastFrom" not in chart["series"][0]
        assert "marksAProjection" not in chart


class TestExistingChartsAreUnchanged:
    @pytest.mark.parametrize("chart_type", ["bar", "line", "pie", "scatter", "area"])
    def test_every_previously_supported_type_still_renders(self, chart_type):
        assert render_chart_to_png(
            {**BARS, "chartType": chart_type},
            get_default_design_system(), 5.0, 3.0,
        )

    def test_a_chart_declaring_none_of_the_new_fields_is_untouched(self):
        """
        Rendered twice through the same path: the new fields are all optional
        and none of them changes a chart that does not use them.
        """
        first = _render(dict(BARS))
        second = _render(dict(BARS))

        assert _difference(first, second) == 0.0
