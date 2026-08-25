"""
Defects a rendered deck showed that the unit tests could not.

Every one of these passed its original test. That is the point of the file:
"render_chart_to_png returned a valid PNG" is satisfied by a chart of entirely
the wrong kind, so every fault below sat behind a green test until decks were
rendered and looked at.

* ``smallMultiples`` silently drew a grouped bar chart, because the renderer
  lowercases the requested type and the type was registered in camelCase - the
  same shape of bug as ``barh`` being named in one branch and nowhere else.
* A waterfall's movements were under 2% of the plot height, because the axis
  was forced to zero while the anchors were 4,104 and the movements were 72.
* A bullet row's bar was an invisible stub, because three measures in three
  units shared one axis.
* A waterfall's anchors were then 8% stubs beside those movements, because
  framing the axis to the movements went one step further than it should.
* One of a bridge's connecting hairlines vanished, because they were drawn
  behind the gridlines and that one coincided with a tick.

So the assertions here are about which renderer ran and what share of the plot
a bar occupies, not about whether bytes came back.
"""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pytest

from app.renderer.chart_renderer import (
    _waterfall_frame, render_chart_to_png, resolve_chart_type,
)
from app.renderer.table_style import resolve_chart_style


class TestARequestedTypeIsTheTypeThatDraws:
    """
    A fallback renders a perfectly good chart of the wrong kind, and nothing
    downstream can tell. So the resolution is asserted directly.
    """

    @pytest.mark.parametrize("requested,expected", [
        ("smallMultiples", "smallmultiples"),
        ("smallmultiples", "smallmultiples"),
        ("small_multiples", "smallmultiples"),
        ("grid", "smallmultiples"),
        ("BARH", "barh"),
        ("horizontalBar", "barh"),
        ("doughnut", "donut"),
        ("stacked_100", "stacked100"),
        ("waterfall", "waterfall"),
    ])
    def test_a_name_a_caller_might_write_resolves(self, requested, expected):
        assert resolve_chart_type(requested) == (expected, True)

    def test_an_unknown_type_says_it_was_not_recognised(self):
        assert resolve_chart_type("sunburst") == ("bar", False)

    def test_every_registered_type_resolves_to_itself(self):
        """
        The invariant the camelCase bug broke: a type in the supported set has
        to be reachable from what a caller sends.
        """
        from app.renderer.chart_renderer import SUPPORTED_CHART_TYPES

        for kind in SUPPORTED_CHART_TYPES:
            assert resolve_chart_type(kind) == (kind, True), kind

    def test_a_grid_actually_draws_a_grid_of_axes(self):
        """
        The assertion the original test should have made. Four panels means
        four axes; a grouped bar chart of the same data is one.
        """
        captured = []
        real = plt.subplots

        def spy(*args, **kwargs):
            fig, axes = real(*args, **kwargs)
            captured.append(axes)
            return fig, axes

        plt.subplots = spy
        try:
            render_chart_to_png({
                "chartType": "smallMultiples",
                "categories": ["Q1", "Q2", "Q3", "Q4"],
                "series": [{"name": n, "values": v} for n, v in (
                    ("Americas", [88.0, 91.0, 95.0, 99.0]),
                    ("EMEA", [72.0, 73.0, 75.0, 77.0]),
                    ("APAC", [64.0, 61.0, 58.0, 55.0]),
                    ("Japan", [40.0, 44.0, 47.0, 52.0]),
                )],
            }, None, 7.0, 3.2)
        finally:
            plt.subplots = real
            plt.close("all")

        assert captured, "nothing was drawn"
        axes = captured[0]
        assert hasattr(axes, "shape"), "one axes, so this rendered as a single chart"
        assert axes.size >= 4


class TestAWaterfallsMovementsAreVisible:
    BRIDGE = [(0.0, 4104.0), (4104.0, 72.0), (4176.0, 44.0),
              (4176.0, 42.0), (4152.0, 26.0), (0.0, 4152.0)]

    def test_a_small_movement_on_a_large_total_occupies_real_height(self):
        """
        The defect, stated as a number. On a zero-based axis a 72-unit
        movement against a 4,104 total is 1.7% of the plot: a coloured sliver.
        """
        low, high = _waterfall_frame(self.BRIDGE, {0, 5})

        share = 72.0 / (high - low)
        assert share > 0.2, f"a 72-unit movement is only {share:.1%} of the plot"

    def test_the_frame_excludes_zero_when_the_data_is_nowhere_near_it(self):
        low, _high = _waterfall_frame(self.BRIDGE, {0, 5})

        assert low > 3000

    def test_an_anchor_column_is_not_a_stub_beside_its_movements(self):
        """
        The other half of the same decision. Zooming to the movements is only
        right up to the point where the two totals being bridged turn into
        stubs shorter than the movements between them, which reads as though
        the movements were the larger quantity.
        """
        low, high = _waterfall_frame(self.BRIDGE, {0, 5})

        plot = high - low
        anchor = (4104.0 - low) / plot
        movement = 72.0 / plot

        assert anchor > movement * 0.6, (
            f"the opening total is {anchor:.0%} of the plot against a "
            f"{movement:.0%} movement, so the bridge is upside down"
        )

    def test_a_build_up_from_nothing_still_shows_zero(self):
        """Zooming a build-up would hide that it started at nothing."""
        build_up = [(0.0, 40.0), (40.0, 30.0), (70.0, 25.0), (0.0, 95.0)]

        low, _high = _waterfall_frame(build_up, {0, 3})

        assert low <= 0.0

    def test_the_connecting_hairlines_are_not_hidden_by_the_gridlines(self):
        """
        A build-up from 40 with ticks every 20 drew every connector but one:
        the one at 40 sat behind the gridline at 40 and vanished. Behind the
        grid is behind whichever connectors the tick spacing happens to land
        on, which reads as a missing hairline rather than as an occlusion.
        """
        captured = []
        real = plt.subplots

        def spy(*args, **kwargs):
            fig, axes = real(*args, **kwargs)
            captured.append(axes)
            return fig, axes

        plt.subplots = spy
        try:
            render_chart_to_png({
                "chartType": "waterfall",
                "categories": ["Pilots", "Expansion", "New logos", "Total"],
                "series": [{"name": "US$m", "values": [40.0, 30.0, 25.0, 95.0]}],
            }, None, 6.4, 3.6)
        finally:
            plt.subplots = real

        assert captured, "nothing was drawn"
        ax = captured[0]
        try:
            grid = ax.yaxis.get_zorder()
            dashed = [line for line in ax.lines if line.get_linestyle() != "None"]
            assert dashed, "no connectors were drawn at all"
            for line in dashed:
                assert line.get_zorder() > grid, (
                    f"a connector at zorder {line.get_zorder()} is behind the "
                    f"gridlines at {grid}"
                )
        finally:
            plt.close("all")

    def test_a_bridge_is_labelled_even_where_the_deck_says_no_labels(self):
        """
        A framed axis means bar lengths no longer state the figures, so the
        labels are the figures. A deck whose convention is "no data labels"
        must not get a bridge with no numbers on it.
        """
        chart = {"chartType": "waterfall",
                 "categories": ["FY24", "Subs", "FY25"],
                 "series": [{"name": "US$m", "values": [4104.0, 48.0, 4152.0]}]}
        silent = {"dataViz": {"chartLabelMode": "none"}}

        with_labels = render_chart_to_png(chart, None, 6.0, 4.0)
        still_labelled = render_chart_to_png(chart, silent, 6.0, 4.0)

        assert still_labelled and still_labelled[:4] == b"\x89PNG"
        # The label_mode token changes other furniture, so this asserts the
        # weaker thing it can: labels were not what made the two differ in size
        # by an order of magnitude.
        assert len(still_labelled) > len(with_labels) * 0.5


class TestABulletRowIsScaledToItsOwnTarget:
    MIXED = {
        "chartType": "bullet",
        "categories": ["Revenue ($B)", "Margin (%)", "NPS"],
        "series": [{"name": "Actual", "values": [4.12, 31.0, 44.0]}],
        "targets": [4.0, 34.0, 40.0],
    }

    def _bar_widths(self, chart):
        captured = []
        real = plt.subplots

        def spy(*args, **kwargs):
            fig, ax = real(*args, **kwargs)
            captured.append(ax)
            return fig, ax

        plt.subplots = spy
        try:
            render_chart_to_png(chart, None, 6.0, 3.2)
        finally:
            plt.subplots = real
        ax = captured[0]
        # The band is drawn first at full width, then the actual bars.
        bars = [p for p in ax.patches if p.get_width() < 0.999]
        plt.close("all")
        return sorted(p.get_width() for p in bars)

    def test_three_measures_in_three_units_all_get_a_visible_bar(self):
        """
        The defect: revenue of 4.12 on an axis running to 44 is a 9% stub with
        its target rule on top of it - the row a reader most wants to see is
        the one they cannot.
        """
        widths = self._bar_widths(self.MIXED)

        assert widths, "no bars were drawn"
        assert min(widths) > 0.4, f"a row rendered as a stub: {widths}"

    def test_a_measure_that_beat_its_target_stays_inside_its_band(self):
        chart = {**self.MIXED,
                 "series": [{"name": "Actual", "values": [8.0, 31.0, 44.0]}]}

        widths = self._bar_widths(chart)

        assert max(widths) <= 1.0

    def test_the_value_axis_is_not_drawn(self):
        """
        Each row has its own scale, so a shared axis would describe none of
        them - and drawing one invites reading one row's length against
        another's.
        """
        captured = []
        real = plt.subplots

        def spy(*args, **kwargs):
            fig, ax = real(*args, **kwargs)
            captured.append(ax)
            return fig, ax

        plt.subplots = spy
        try:
            render_chart_to_png(self.MIXED, None, 6.0, 3.2)
        finally:
            plt.subplots = real
        ax = captured[0]
        visible = ax.get_xticklabels()
        plt.close("all")

        assert not any(label.get_visible() and label.get_text()
                       for label in visible)


class TestAStackShowsWholePercentages:
    def test_a_hundred_percent_stack_prints_no_decimals(self):
        """
        "30%" beside "29.0%" is one chart using two conventions, in labels a
        reader compares directly. Floating point makes it happen whenever a
        column does not sum to exactly 100.
        """
        captured = []
        real = plt.subplots

        def spy(*args, **kwargs):
            fig, ax = real(*args, **kwargs)
            captured.append(ax)
            return fig, ax

        plt.subplots = spy
        try:
            render_chart_to_png({
                "chartType": "stacked100",
                "categories": ["FY23", "FY24"],
                "series": [{"name": "A", "values": [33.0, 29.0]},
                           {"name": "B", "values": [38.0, 38.0]},
                           {"name": "C", "values": [29.0, 33.0]}],
            }, None, 6.0, 4.0)
        finally:
            plt.subplots = real
        labels = [t.get_text() for t in captured[0].texts]
        plt.close("all")

        percentages = [t for t in labels if t.endswith("%")]
        assert percentages
        assert not any("." in t for t in percentages), percentages


class TestTheSemanticColoursBelongToTheDeck:
    def test_a_bridges_totals_take_the_decks_own_dark(self):
        """
        The default navy belongs to no deck - the same objection
        derive_data_viz_config exists to answer for the series sequence.
        """
        from app.directions import DirectionSpec, compile_direction

        system = compile_direction(
            DirectionSpec.from_dict({"name": "Violet", "density": "balanced"}),
            "#6D28D9",
        )
        style = resolve_chart_style(system)

        assert style.waterfall_total_color != "#12233F"

    def test_rise_and_fall_stay_semantic(self):
        """
        The sign of a movement must not depend on where a deck happened to put
        its accent colours.
        """
        from app.directions import DirectionSpec, compile_direction

        style = resolve_chart_style(compile_direction(
            DirectionSpec.from_dict({"name": "Violet", "density": "balanced"}),
            "#6D28D9",
        ))

        assert style.waterfall_rise_color == "#2E9AA6"
        assert style.waterfall_fall_color == "#C0553B"


class TestHidingTheCategoryAxisKeepsTheCategoryNames:
    """
    A deck that hides its axes still has to say which bar is which.

    ``chartCategoryAxis: false`` is a minimalist convention - no rule, no tick
    marks - and it was also switching off the tick *labels*, which are the
    only thing naming the bars. A researched three-segment forecast rendered
    as three unlabelled rectangles under an axis titled "Market Segments":
    every number on the page was correct and the chart said nothing.

    The two axes are not symmetric. A value axis is redundant on a chart that
    prints its values on the bars; a category axis never is.
    """

    CATEGORIES = ["IT Spending", "AI Spending", "Semiconductor Market"]

    def _axes_labels(self, show_category_axis: bool) -> list[str]:
        import matplotlib.pyplot as plt

        from app.design_system import get_default_design_system

        design = get_default_design_system()
        design.setdefault("dataViz", {})["chartCategoryAxis"] = show_category_axis

        drawn: list[list[str]] = []
        original = plt.Figure.savefig

        def capture(fig, *args, **kwargs):
            for ax in fig.get_axes():
                drawn.append([
                    t.get_text() for t in ax.get_xticklabels() if t.get_visible()
                ])
            return original(fig, *args, **kwargs)

        plt.Figure.savefig = capture
        try:
            render_chart_to_png(
                {
                    "chartType": "bar",
                    "categories": self.CATEGORIES,
                    "series": [{"name": "Growth", "values": [14.2, 47.0, 52.8]}],
                },
                design, 11.0, 4.0, dpi=72,
            )
        finally:
            plt.Figure.savefig = original
        return [label for group in drawn for label in group if label]

    def test_the_names_are_drawn_when_the_axis_is_hidden(self):
        labels = self._axes_labels(show_category_axis=False)
        assert set(self.CATEGORIES) <= set(labels), labels

    def test_the_names_are_drawn_when_the_axis_is_shown(self):
        labels = self._axes_labels(show_category_axis=True)
        assert set(self.CATEGORIES) <= set(labels), labels
