"""
Tests for the dataViz addition to extract_template_design_system - table/
chart styling tokens derived (XML path) or LLM-judged (vision/PDF path) so
generated tables/charts pick up a template's own brand colors.
"""

import pytest

from app.agent.tools.extract_template_design_system import ExtractTemplateDesignSystemTool


class TestParseVisionResponseDataViz:
    """_parse_vision_response is a pure function - no I/O - so these test
    the merge/fallback logic directly without needing a real vision call."""

    def test_uses_llm_provided_data_viz_when_present(self):
        import json

        response = json.dumps({
            "palette": {"colors": [{"name": "primary", "value": "#00704A", "type": "hex"}], "schemeName": "brand"},
            "dataViz": {
                "chartColorSequence": ["#00704A", "#1E3932"],
                "tableHeaderBg": "#00704A",
                "tableHeaderFg": "#FFFFFF",
                "fontFamily": "Calibri",
            },
        })
        result = ExtractTemplateDesignSystemTool._parse_vision_response(response)
        assert result["dataViz"]["chartColorSequence"] == ["#00704A", "#1E3932"]
        assert result["dataViz"]["tableHeaderBg"] == "#00704A"

    def test_falls_back_to_derived_config_when_data_viz_missing(self):
        import json

        response = json.dumps({
            "palette": {"colors": [{"name": "primary", "value": "#0000FF", "type": "hex"}], "schemeName": "s"},
        })
        result = ExtractTemplateDesignSystemTool._parse_vision_response(response)
        assert "dataViz" in result
        assert result["dataViz"]["tableHeaderBg"] == "#0000FF"

    def test_falls_back_when_data_viz_present_but_missing_color_sequence(self):
        """A malformed/incomplete dataViz from the LLM (e.g. missing the one
        field everything else depends on) must not be trusted as-is."""
        import json

        response = json.dumps({
            "palette": {"colors": [{"name": "primary", "value": "#ABCDEF", "type": "hex"}], "schemeName": "s"},
            "dataViz": {"tableHeaderBg": "#ABCDEF"},  # no chartColorSequence
        })
        result = ExtractTemplateDesignSystemTool._parse_vision_response(response)
        assert result["dataViz"]["chartColorSequence"], "must derive a usable sequence, not leave it empty"

    def test_completely_unparseable_response_still_yields_a_data_viz(self):
        result = ExtractTemplateDesignSystemTool._parse_vision_response("not json at all")
        assert "dataViz" in result
        assert result["dataViz"]["chartColorSequence"]


class TestExtractFromPptxDataViz:
    @pytest.mark.asyncio
    async def test_pptx_extraction_includes_data_viz_derived_from_its_own_palette(self, tmp_path):
        from app.renderer.default_template import get_default_template_path

        # The bundled default template is a real .pptx we can extract from
        # without needing a hand-authored fixture file.
        template_path = get_default_template_path(output_dir=str(tmp_path / "templates"))
        design_system = await ExtractTemplateDesignSystemTool._extract_from_pptx(template_path)

        assert design_system["sourceType"] == "pptx"
        assert "dataViz" in design_system
        assert design_system["dataViz"]["chartColorSequence"]
        assert design_system["dataViz"]["tableHeaderBg"].startswith("#")
