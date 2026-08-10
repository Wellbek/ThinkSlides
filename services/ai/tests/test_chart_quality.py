"""
Charts have to be readable as pictures, because that is all they are.

Issue #56's G4-G6: the renderer drew matplotlib defaults with a brand
palette and nothing else - no value-axis label, no data labels, no pie
shares, and the legend suppressed for the single-series case. The rendered
revenue chart's y-axis read "24 ... 36" while the unit ($B) lived only in
the series name, which was the one string never drawn.

These assert against the figure matplotlib actually builds, rather than the
PNG bytes, so a failure names the missing element.
"""

import io

import pytest
from PIL import Image

from app.renderer import chart_renderer


@pytest.fixture(autouse=True)
def _close_figures():
    import matplotlib.pyplot as plt
    yield
    plt.close("all")


def _figure(chart_data, width_in=9.0, height_in=3.6, design_system=None, slide_title=None):
    """Build the chart and hand back the live matplotlib figure."""
    import matplotlib.pyplot as plt

    captured = {}
    original = plt.subplots

    def spy(*args, **kwargs):
        fig, ax = original(*args, **kwargs)
        captured["fig"], captured["ax"] = fig, ax
        return fig, ax

    plt.subplots = spy
    try:
        png = chart_renderer.render_chart_to_png(
            chart_data, design_system, width_in, height_in, slide_title=slide_title,
        )
    finally:
        plt.subplots = original
    assert png, "chart failed to render"
    return captured["ax"], png


def _texts(ax):
    return [t.get_text() for t in ax.texts]


REVENUE = {
    "chartType": "line",
    "categories": ["2021", "2022", "2023", "2024", "2025"],
    "series": [{"name": "Revenue ($B)", "values": [29.1, 32.3, 36.0, 36.2, 36.2]}],
}


class TestTheUnitIsAlwaysVisible:
    def test_a_single_series_name_becomes_the_value_axis_label(self):
        """The exact case that suppressed the legend and lost "$B"."""
        ax, _ = _figure(REVENUE)

        assert ax.get_ylabel() == "Revenue ($B)"

    def test_an_explicit_axis_title_wins_over_the_series_name(self):
        ax, _ = _figure({**REVENUE, "yAxisTitle": "Net revenue, US$ billions"})

        assert ax.get_ylabel() == "Net revenue, US$ billions"

    def test_a_category_axis_title_is_drawn_when_given(self):
        ax, _ = _figure({**REVENUE, "xAxisTitle": "Fiscal year"})

        assert ax.get_xlabel() == "Fiscal year"

    def test_multiple_series_get_a_legend_instead(self):
        ax, _ = _figure({
            "chartType": "bar",
            "categories": ["Q1", "Q2"],
            "series": [
                {"name": "2024", "values": [3, 4]},
                {"name": "2025", "values": [5, 6]},
            ],
        })

        legend = ax.get_legend()
        assert legend is not None
        assert [t.get_text() for t in legend.get_texts()] == ["2024", "2025"]


class TestDataLabels:
    def test_every_point_is_labelled_with_its_value(self):
        ax, _ = _figure(REVENUE)

        labels = _texts(ax)
        assert "29.1" in labels
        assert "36.2" in labels

    def test_a_unit_suffix_reaches_the_labels_and_the_axis(self):
        ax, _ = _figure({
            "chartType": "bar",
            "categories": ["A", "B"],
            "series": [{"name": "Share", "values": [61.0, 39.0]}],
            "valueFormat": "percent",
        })

        assert "61%" in _texts(ax)

    def test_a_dense_series_is_left_to_the_axis(self):
        """Per-point labels past a certain density are an unreadable smear."""
        ax, _ = _figure({
            "chartType": "line",
            "categories": [str(i) for i in range(24)],
            "series": [{"name": "Visits", "values": list(range(24))}],
        })

        assert _texts(ax) == []

    def test_labels_can_be_switched_off(self):
        ax, _ = _figure({**REVENUE, "showDataLabels": False})

        assert _texts(ax) == []


class TestPieCharts:
    PIE = {
        "chartType": "pie",
        "categories": ["North America", "International", "Channel Development"],
        "series": [{"name": "Share of revenue", "values": [74.0, 21.0, 5.0]}],
    }

    def test_each_slice_states_its_share(self):
        """A pie whose slices are named but not quantified withholds the one
        number a reader wants from it."""
        ax, _ = _figure(self.PIE)

        entries = [t.get_text() for t in ax.get_legend().get_texts()]
        assert entries == [
            "North America - 74%",
            "International - 21%",
            "Channel Development - 5%",
        ]

    def test_a_long_tail_is_grouped_rather_than_shredded(self):
        ax, _ = _figure({
            "chartType": "pie",
            "categories": [f"C{i}" for i in range(12)],
            "series": [{"name": "Share", "values": [50] + [5] * 11}],
        })

        entries = [t.get_text() for t in ax.get_legend().get_texts()]
        assert len(entries) == chart_renderer.MAX_PIE_SLICES
        assert entries[-1].startswith("Other")

    def test_zero_total_degrades_to_no_chart_rather_than_raising(self):
        png = chart_renderer.render_chart_to_png(
            {"chartType": "pie", "categories": ["A"], "series": [{"name": "x", "values": [0]}]},
            None, 6.0, 3.0,
        )

        assert png is None


class TestGeometry:
    @pytest.mark.parametrize("width_in,height_in", [(9.0, 3.0), (4.0, 4.0), (11.63, 2.2)])
    def test_the_png_keeps_the_aspect_ratio_it_was_asked_for(self, width_in, height_in):
        """`bbox_inches="tight"` cropped to the drawn extent, so the PNG's
        aspect no longer matched the figsize - and both placement paths then
        forced it back into the requested rectangle, stretching every chart
        and its type non-uniformly."""
        png = chart_renderer.render_chart_to_png(
            {"chartType": "bar", "categories": ["A", "B"],
             "series": [{"name": "n", "values": [1, 2]}]},
            None, width_in, height_in,
        )
        image = Image.open(io.BytesIO(png))

        assert image.size[0] / image.size[1] == pytest.approx(width_in / height_in, rel=0.01)


class TestAxisHonesty:
    def test_a_bar_chart_starts_at_zero(self):
        """A bar's length is its value, so a cropped baseline misstates the
        comparison it exists to make."""
        ax, _ = _figure({
            "chartType": "bar",
            "categories": ["A", "B"],
            "series": [{"name": "n", "values": [98.0, 100.0]}],
        })

        assert ax.get_ylim()[0] <= 0

    def test_a_line_chart_may_zoom(self):
        """Line charts show change, where a non-zero baseline is legitimate."""
        ax, _ = _figure(REVENUE)

        assert ax.get_ylim()[0] > 0


class TestChartTitles:
    def test_a_title_that_restates_the_slide_heading_is_dropped(self):
        ax, _ = _figure({**REVENUE, "title": "Revenue trajectory"},
                        slide_title="Revenue trajectory")

        assert ax.get_title(loc="left") == ""

    def test_a_title_that_adds_something_is_kept(self):
        ax, _ = _figure({**REVENUE, "title": "FY21-FY25, reported"},
                        slide_title="Revenue trajectory")

        assert ax.get_title(loc="left") == "FY21-FY25, reported"


class TestRobustness:
    def test_non_numeric_values_do_not_abort_the_render(self):
        png = chart_renderer.render_chart_to_png(
            {"chartType": "bar", "categories": ["A", "B"],
             "series": [{"name": "n", "values": ["1.5", None]}]},
            None, 6.0, 3.0,
        )

        assert png

    def test_a_wholly_unusable_series_degrades_to_no_chart(self):
        png = chart_renderer.render_chart_to_png(
            {"chartType": "bar", "categories": ["A"],
             "series": [{"name": "n", "values": ["nope"]}]},
            None, 6.0, 3.0,
        )

        assert png is None

    def test_an_unknown_chart_type_falls_back_to_bar(self):
        png = chart_renderer.render_chart_to_png(
            {"chartType": "sunburst", "categories": ["A"],
             "series": [{"name": "n", "values": [1]}]},
            None, 6.0, 3.0,
        )

        assert png
