"""
Typed constructors for a slide's visuals.

Charts and tables used to reach the deck only as free-form JSON inside
design_slide_content's single response, described in prose in the middle of
a long prompt. A series with fewer values than categories, a "36.2B" where a
number belonged, a ragged row - none of it was detectable until the renderer
silently dropped the item or drew something wrong.

The point of these tools is that a malformed visual fails *where it was
stated*, with a message the model can act on.
"""

import pytest

from app.agent.tools.build_visuals import (
    BuildChartTool,
    BuildKeyMetricsTool,
    BuildTableTool,
)

pytestmark = pytest.mark.asyncio


async def _chart(**kwargs):
    return await BuildChartTool.run(**{
        "slide_index": 2,
        "chart_type": "line",
        "categories": ["2023", "2024", "2025"],
        "series": [{"name": "Revenue ($B)", "values": [36.0, 36.2, 36.2]}],
        **kwargs,
    })


class TestBuildChart:
    async def test_a_well_formed_chart_becomes_a_content_block(self):
        result = await _chart()

        assert result.success
        assert result.data["slideIndex"] == 2
        assert result.data["content"]["type"] == "chart"
        assert result.data["content"]["chart"]["chartType"] == "line"

    async def test_numeric_strings_are_coerced(self):
        """Tool-calling models routinely send "36.2" for a number field."""
        result = await _chart(series=[{"name": "Revenue ($B)",
                                       "values": ["36.0", "36.2", "36.2"]}])

        assert result.success
        assert result.data["content"]["chart"]["series"][0]["values"] == [36.0, 36.2, 36.2]

    async def test_a_unit_inside_a_value_is_refused_with_an_actionable_message(self):
        result = await _chart(series=[{"name": "Revenue", "values": ["36.2B", "1", "2"]}])

        assert not result.success
        assert "36.2B" in result.error
        assert "series name" in result.error

    async def test_a_series_length_mismatch_names_both_counts(self):
        """The failure that silently produced a wrong chart."""
        result = await _chart(series=[{"name": "Revenue", "values": [1, 2]}])

        assert not result.success
        assert "2 values" in result.error
        assert "3 categories" in result.error

    async def test_a_series_without_a_name_is_refused(self):
        """The name is the only place the unit can live for one series."""
        result = await _chart(series=[{"values": [1, 2, 3]}])

        assert not result.success
        assert "name" in result.error

    async def test_a_pie_with_several_series_is_refused(self):
        result = await _chart(chart_type="pie", series=[
            {"name": "A", "values": [1, 2, 3]},
            {"name": "B", "values": [3, 2, 1]},
        ])

        assert not result.success
        assert "ONE whole" in result.error

    async def test_a_pie_with_negative_values_is_refused(self):
        result = await _chart(chart_type="pie",
                              series=[{"name": "Share", "values": [-1, 2, 3]}])

        assert not result.success
        assert "negative" in result.error

    async def test_an_unsupported_chart_type_lists_the_supported_ones(self):
        result = await _chart(chart_type="sunburst")

        assert not result.success
        assert "scatter" in result.error

    async def test_empty_categories_are_refused(self):
        result = await _chart(categories=[])

        assert not result.success

    async def test_too_many_categories_to_read_are_refused(self):
        result = await _chart(
            categories=[str(i) for i in range(40)],
            series=[{"name": "n", "values": list(range(40))}],
        )

        assert not result.success
        assert "too many" in result.error

    async def test_axis_titles_and_format_are_carried_through(self):
        result = await _chart(y_axis_title="Net revenue (US$bn)",
                              x_axis_title="Fiscal year", value_format="currency")
        chart = result.data["content"]["chart"]

        assert chart["yAxisTitle"] == "Net revenue (US$bn)"
        assert chart["xAxisTitle"] == "Fiscal year"
        assert chart["valueFormat"] == "currency"

    async def test_json_encoded_arguments_are_accepted(self):
        """Models send a JSON string for array parameters often enough that
        refusing it would just cost a retry."""
        result = await BuildChartTool.run(
            slide_index=0, chart_type="bar",
            categories='["A", "B"]',
            series='[{"name": "n", "values": [1, 2]}]',
        )

        assert result.success


class TestBuildTable:
    async def test_columns_become_the_header_row(self):
        result = await BuildTableTool.run(
            slide_index=4,
            columns=["Segment", "Revenue ($B)"],
            rows=[["North America", "26.8"], ["International", "7.6"]],
        )

        assert result.success
        table = result.data["content"]["table"]
        assert table["headerRow"] is True
        assert table["rows"][0] == ["Segment", "Revenue ($B)"]
        assert len(table["rows"]) == 3

    async def test_a_ragged_row_is_refused_with_its_position(self):
        result = await BuildTableTool.run(
            slide_index=0, columns=["A", "B", "C"],
            rows=[["1", "2", "3"], ["4", "5"]],
        )

        assert not result.success
        assert "rows[1]" in result.error

    async def test_a_row_sent_as_an_object_is_read_in_column_order(self):
        result = await BuildTableTool.run(
            slide_index=0, columns=["Segment", "Revenue"],
            rows=[{"Revenue": "26.8", "Segment": "North America"}],
        )

        assert result.success
        assert result.data["content"]["table"]["rows"][1] == ["North America", "26.8"]

    async def test_a_single_column_table_is_refused(self):
        result = await BuildTableTool.run(slide_index=0, columns=["A"], rows=[["1"]])

        assert not result.success
        assert "bullets" in result.error

    async def test_too_many_rows_for_a_slide_are_refused(self):
        result = await BuildTableTool.run(
            slide_index=0, columns=["A", "B"],
            rows=[[str(i), str(i)] for i in range(40)],
        )

        assert not result.success


class TestBuildKeyMetrics:
    async def test_figures_become_a_metrics_block(self):
        result = await BuildKeyMetricsTool.run(
            slide_index=1,
            metrics=[{"value": "$36.2B", "label": "FY2025 revenue"},
                     {"value": "+3%", "label": "Year over year"}],
        )

        assert result.success
        assert result.data["content"]["type"] == "metrics"
        assert len(result.data["content"]["metrics"]["items"]) == 2

    async def test_a_lone_figure_is_refused(self):
        result = await BuildKeyMetricsTool.run(
            slide_index=0, metrics=[{"value": "3%", "label": "Growth"}]
        )

        assert not result.success

    async def test_a_figure_without_a_label_is_refused(self):
        """A number with nothing saying what it measures is not information."""
        result = await BuildKeyMetricsTool.run(
            slide_index=0,
            metrics=[{"value": "3%", "label": ""}, {"value": "4%", "label": "Margin"}],
        )

        assert not result.success
        assert "label" in result.error

    async def test_more_than_four_figures_are_refused(self):
        result = await BuildKeyMetricsTool.run(
            slide_index=0,
            metrics=[{"value": str(i), "label": f"L{i}"} for i in range(6)],
        )

        assert not result.success
