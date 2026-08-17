"""
A design direction draws its charts differently, not just recoloured.

The style tokens have existed since #120 - gridlines, label mode, legend,
unit convention, bar geometry - and nothing authored them. So three visibly
different directions all drew the same matplotlib chart, in the one element of
a slide a reader is most often actually looking at.

A family is a coherent SET of those tokens rather than six independent
switches, and that is the point: a deck that drops its value axis has to label
every bar, or the numbers are simply gone.
"""

import pytest

from app.directions import (
    CHART_FAMILIES_BY_DENSITY, DirectionSpec, compile_direction,
)
from app.renderer.table_style import (
    CHART_FAMILIES, CHART_FAMILY_NAMES, apply_chart_family, resolve_chart_style,
)


def _spec(**kwargs):
    return DirectionSpec.from_dict({"name": "A direction", **kwargs})


class TestTheFamiliesAreCoherent:
    @pytest.mark.parametrize("family", CHART_FAMILY_NAMES)
    def test_a_family_that_drops_the_axis_labels_every_point(self, family):
        """
        Otherwise the numbers are gone. This is why a family is a set rather
        than six switches a model picks between.
        """
        tokens = CHART_FAMILIES[family]
        if tokens["chartValueAxis"] is False:
            assert tokens["chartLabelMode"] in ("all", "end")

    @pytest.mark.parametrize("family", CHART_FAMILY_NAMES)
    def test_every_family_uses_real_token_values(self, family):
        """A value outside the allowed set is silently ignored, not an error."""
        from app.renderer.table_style import (
            LABEL_MODES, LEGEND_STYLES, UNIT_CONVENTIONS,
        )

        tokens = CHART_FAMILIES[family]
        assert tokens["chartLabelMode"] in LABEL_MODES
        assert tokens["chartLegend"] in LEGEND_STYLES
        assert tokens["chartUnitConvention"] in UNIT_CONVENTIONS

    def test_the_three_families_are_genuinely_different(self):
        shapes = set()
        for family in CHART_FAMILY_NAMES:
            style = resolve_chart_style({"dataViz": {"chartFamily": family}})
            shapes.add((style.show_value_axis, style.show_value_gridlines,
                        style.label_mode, style.legend_style))

        assert len(shapes) == len(CHART_FAMILY_NAMES)


class TestPrecedence:
    def test_a_measured_token_outranks_a_family(self):
        """
        A design system's tokens were measured off a real template or written
        by hand. Both outrank a family name.
        """
        style = resolve_chart_style({
            "dataViz": {"chartFamily": "minimal", "chartValueAxis": True}
        })

        assert style.show_value_axis is True

    def test_a_family_outranks_a_derived_default(self):
        """
        derive_data_viz_config states every token as the value the renderer
        already used - defaults made changeable, not authorial choices. A
        direction that named a family and drew the default chart anyway is the
        bug this ordering fixes.
        """
        derived = {"chartValueAxis": True, "chartLabelMode": "auto"}

        merged = apply_chart_family(derived, "minimal", wins=True)

        assert merged["chartValueAxis"] is False
        assert merged["chartLabelMode"] == "all"

    def test_an_unknown_family_changes_nothing(self):
        derived = {"chartValueAxis": True}

        assert apply_chart_family(derived, "sunburst", wins=True) == derived

    def test_no_family_named_leaves_a_design_system_untouched(self):
        """Every design system written before this existed."""
        plain = resolve_chart_style(None)

        assert plain.show_value_axis is True
        assert plain.label_mode == "auto"


class TestADirectionCarriesItsFamily:
    @pytest.mark.parametrize("density,expected", sorted(
        CHART_FAMILIES_BY_DENSITY.items()
    ))
    def test_it_follows_the_density_by_default(self, density, expected):
        """
        Paired rather than free: a deck whose points are oversized figures and
        whose charts are gridded analyst plots is two decks.
        """
        assert _spec(density=density).resolved_chart_family == expected

    def test_a_named_family_overrides_the_density(self):
        assert _spec(density="airy", chartFamily="gridded").resolved_chart_family == (
            "gridded"
        )

    def test_an_invented_family_falls_back_to_the_density(self):
        assert _spec(density="dense", chartFamily="sunburst").resolved_chart_family == (
            "gridded"
        )

    def test_it_survives_a_round_trip_through_to_dict(self):
        """
        The colour is answered after the specimens are drawn, so a direction
        is recompiled from its own dict - anything not in there is lost.
        """
        spec = _spec(density="airy", chartFamily="gridded")

        assert DirectionSpec.from_dict(spec.to_dict()).resolved_chart_family == (
            "gridded"
        )

    def test_it_reaches_the_compiled_design_system(self):
        system = compile_direction(_spec(density="dense"), "#12233F")

        assert system["dataViz"]["chartFamily"] == "gridded"
        assert system["dataViz"]["chartValueGridlines"] is True

    def test_three_directions_compile_to_three_chart_treatments(self):
        """The whole point: the previews differ in how they draw a chart."""
        treatments = set()
        for density in ("airy", "balanced", "dense"):
            system = compile_direction(_spec(density=density), "#12233F")
            style = resolve_chart_style(system)
            treatments.add((style.show_value_axis, style.show_value_gridlines,
                            style.label_mode, style.legend_style))

        assert len(treatments) == 3

    def test_the_palette_still_reaches_the_chart(self):
        """A family sets the furniture, never the colours."""
        system = compile_direction(_spec(density="dense"), "#12233F")
        style = resolve_chart_style(system)

        assert style.color_sequence
        assert style.color_sequence[0].upper() != "#1F4E78"


class TestTheSemanticColoursAreReadable:
    def test_a_declared_rise_colour_reaches_the_style(self):
        style = resolve_chart_style({"dataViz": {"chartRiseColor": "#2E9AA6"}})

        assert style.waterfall_rise_color == "#2E9AA6"

    def test_a_malformed_colour_falls_back(self):
        style = resolve_chart_style({"dataViz": {"chartFallColor": "not a colour"}})

        assert style.waterfall_fall_color == "#C0553B"

    def test_a_bare_hex_is_accepted(self):
        style = resolve_chart_style({"dataViz": {"chartTotalColor": "12233F"}})

        assert style.waterfall_total_color == "#12233F"
