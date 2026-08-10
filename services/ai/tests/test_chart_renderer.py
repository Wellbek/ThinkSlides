"""
Tests for app.renderer.chart_renderer - matplotlib-based chart-to-PNG
rendering. Charts were previously schema-legal (validate_slide_json accepted
"chart" content, the shared ChartContent pydantic model existed) but nothing
in the renderer ever produced pixels for one.
"""

import pytest

from app.renderer.chart_renderer import render_chart_to_png, SUPPORTED_CHART_TYPES

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

BRAND_DESIGN_SYSTEM = {
    "palette": {"colors": [{"name": "primary", "value": "#00704A", "type": "hex"}]},
    "dataViz": {
        "chartColorSequence": ["#00704A", "#1E3932", "#D4E9E2"],
        "chartAxisColor": "#1E3932",
        "chartGridColor": "#D4E9E2",
        "fontFamily": "Arial",
    },
}

BAR_CHART = {
    "chartType": "bar",
    "categories": ["Q1", "Q2", "Q3"],
    "series": [{"name": "Revenue", "values": [10, 20, 15]}],
}


@pytest.mark.parametrize("chart_type", sorted(SUPPORTED_CHART_TYPES))
def test_each_supported_chart_type_renders_a_valid_png(chart_type):
    chart_data = {
        "chartType": chart_type,
        "categories": ["A", "B", "C"],
        "series": [{"name": "Series 1", "values": [3, 7, 5]}],
    }
    png_bytes = render_chart_to_png(chart_data, BRAND_DESIGN_SYSTEM, width_in=6, height_in=4)
    assert png_bytes is not None
    assert png_bytes.startswith(PNG_MAGIC)


def test_multi_series_bar_chart_renders():
    chart_data = {
        "chartType": "bar",
        "categories": ["Q1", "Q2"],
        "series": [
            {"name": "EMEA", "values": [10, 12]},
            {"name": "APAC", "values": [8, 9]},
        ],
    }
    png_bytes = render_chart_to_png(chart_data, BRAND_DESIGN_SYSTEM, width_in=6, height_in=4)
    assert png_bytes is not None
    assert png_bytes.startswith(PNG_MAGIC)


def test_unsupported_chart_type_falls_back_to_bar_instead_of_failing():
    chart_data = {"chartType": "radar", "categories": ["A", "B"], "series": [{"values": [1, 2]}]}
    png_bytes = render_chart_to_png(chart_data, BRAND_DESIGN_SYSTEM, width_in=6, height_in=4)
    assert png_bytes is not None


def test_empty_series_returns_none_not_raises():
    assert render_chart_to_png({"chartType": "bar", "series": []}, BRAND_DESIGN_SYSTEM, 6, 4) is None
    assert render_chart_to_png({"chartType": "bar", "series": [{"values": []}]}, BRAND_DESIGN_SYSTEM, 6, 4) is None


def test_missing_series_key_returns_none_not_raises():
    assert render_chart_to_png({"chartType": "bar"}, BRAND_DESIGN_SYSTEM, 6, 4) is None


def test_zero_size_box_returns_none():
    assert render_chart_to_png(BAR_CHART, BRAND_DESIGN_SYSTEM, width_in=0, height_in=4) is None
    assert render_chart_to_png(BAR_CHART, BRAND_DESIGN_SYSTEM, width_in=6, height_in=0) is None


def test_no_design_system_still_renders_with_default_colors():
    png_bytes = render_chart_to_png(BAR_CHART, None, width_in=6, height_in=4)
    assert png_bytes is not None
    assert png_bytes.startswith(PNG_MAGIC)


def test_malformed_series_item_does_not_crash():
    """A non-dict item in `series` (bad LLM output) must degrade gracefully."""
    chart_data = {"chartType": "bar", "categories": ["A"], "series": ["not-a-dict"]}
    # Either renders nothing meaningful (None) or doesn't raise - the
    # contract is "never crash the whole render over a malformed chart".
    result = render_chart_to_png(chart_data, BRAND_DESIGN_SYSTEM, 6, 4)
    assert result is None or result.startswith(PNG_MAGIC)
