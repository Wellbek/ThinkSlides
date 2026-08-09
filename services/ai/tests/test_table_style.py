"""
Tests for app.renderer.table_style - resolves table/chart styling from a
run's design system, preferring an explicit `dataViz` config and falling
back to deriving from the base palette when it isn't present (e.g. an
older DesignSystemBundle row written before that column existed).
"""

from app.renderer.table_style import resolve_table_style, resolve_chart_style, DEFAULT_HEADER_BG


class TestResolveTableStyle:
    def test_none_design_system_uses_hardcoded_defaults(self):
        style = resolve_table_style(None)
        assert style.header_bg == DEFAULT_HEADER_BG
        assert style.header_fg == "#FFFFFF"

    def test_prefers_explicit_data_viz_over_palette(self):
        design_system = {
            "palette": {"colors": [{"name": "primary", "value": "#0000FF"}]},
            "dataViz": {"tableHeaderBg": "#00704A", "tableHeaderFg": "#FFFFFF", "fontFamily": "Georgia"},
        }
        style = resolve_table_style(design_system)
        assert style.header_bg == "#00704A"
        assert style.font_name == "Georgia"

    def test_falls_back_to_palette_primary_when_no_data_viz(self):
        design_system = {"palette": {"colors": [{"name": "primary", "value": "#0000FF"}]}}
        style = resolve_table_style(design_system)
        assert style.header_bg == "#0000FF"

    def test_falls_back_to_body_font_when_no_data_viz(self):
        design_system = {
            "palette": {"colors": []},
            "typography": {"bodyFont": {"typeface": "Georgia"}},
        }
        style = resolve_table_style(design_system)
        assert style.font_name == "Georgia"

    def test_empty_data_viz_dict_falls_back_to_palette(self):
        """An old DesignSystemBundle row with data_viz={} (server_default
        before backfill) must not be treated as "explicit dataViz present"."""
        design_system = {
            "palette": {"colors": [{"name": "primary", "value": "#123456"}]},
            "dataViz": {},
        }
        style = resolve_table_style(design_system)
        assert style.header_bg == "#123456"


class TestResolveChartStyle:
    def test_prefers_explicit_color_sequence(self):
        design_system = {"dataViz": {"chartColorSequence": ["#111111", "#222222"]}}
        style = resolve_chart_style(design_system)
        assert style.color_sequence == ["#111111", "#222222"]

    def test_falls_back_to_palette_colors_in_order(self):
        design_system = {
            "palette": {"colors": [{"name": "c1", "value": "#111111"}, {"name": "c2", "value": "#222222"}]}
        }
        style = resolve_chart_style(design_system)
        assert style.color_sequence == ["#111111", "#222222"]

    def test_falls_back_to_hardcoded_sequence_when_nothing_available(self):
        style = resolve_chart_style(None)
        assert len(style.color_sequence) >= 1
        assert all(c.startswith("#") for c in style.color_sequence)

    def test_palette_sequence_is_capped_at_five(self):
        colors = [{"name": f"c{i}", "value": f"#{i:06x}"} for i in range(10)]
        style = resolve_chart_style({"palette": {"colors": colors}})
        assert len(style.color_sequence) == 5
