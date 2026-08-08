"""
Tests for the PPTX renderer.
"""

import pytest
import os
import tempfile
from app.renderer.pptx_renderer import PPTXRenderer
from app.design_system import COLOR_THEMES
from app.renderer.default_template import _hex_to_rgbcolor


class TestPPTXRendererDefault:
    """Test rendering with the default theme (no template)."""

    @pytest.fixture
    def renderer(self):
        return PPTXRenderer(template_path=None)

    def test_render_empty_presentation(self, renderer):
        """Test rendering a presentation with no slides."""
        with tempfile.NamedTemporaryFile(suffix=".pptx", delete=False) as f:
            output_path = f.name

        try:
            result = renderer.render(
                slides=[],
                metadata={"title": "Test"},
                output_path=output_path,
            )

            assert os.path.exists(result)
            assert os.path.getsize(result) > 0
        finally:
            os.unlink(output_path)

    def test_render_single_slide(self, renderer):
        """Test rendering a single text slide."""
        slides = [
            {
                "slideIndex": 0,
                "layoutId": "1",
                "layoutName": "Default",
                "contents": [
                    {
                        "type": "text",
                        "placeholderIdx": 0,
                        "runs": [
                            {"text": "Hello World", "bold": True},
                        ],
                    }
                ],
            }
        ]

        with tempfile.NamedTemporaryFile(suffix=".pptx", delete=False) as f:
            output_path = f.name

        try:
            result = renderer.render(
                slides=slides,
                metadata={"title": "Test Presentation", "author": "Test Author"},
                output_path=output_path,
            )

            assert os.path.exists(result)
            assert os.path.getsize(result) > 0

            # Verify it's a valid PPTX by opening with python-pptx
            from pptx import Presentation
            prs = Presentation(result)
            assert len(prs.slides) == 1
        finally:
            os.unlink(output_path)

    def test_render_multiple_slides(self, renderer):
        """Test rendering multiple slides."""
        slides = [
            {
                "slideIndex": 0,
                "layoutId": "1",
                "layoutName": "Default",
                "contents": [
                    {
                        "type": "text",
                        "placeholderIdx": 0,
                        "runs": [{"text": "Title Slide", "bold": True}],
                    }
                ],
            },
            {
                "slideIndex": 1,
                "layoutId": "1",
                "layoutName": "Default",
                "contents": [
                    {
                        "type": "text",
                        "placeholderIdx": 0,
                        "runs": [
                            {"text": "Point 1", "bullet": True, "level": 0},
                            {"text": "Point 2", "bullet": True, "level": 0},
                        ],
                    }
                ],
            },
        ]

        with tempfile.NamedTemporaryFile(suffix=".pptx", delete=False) as f:
            output_path = f.name

        try:
            result = renderer.render(
                slides=slides,
                metadata={"title": "Multi-Slide Test"},
                output_path=output_path,
            )

            assert os.path.exists(result)
            from pptx import Presentation
            prs = Presentation(result)
            assert len(prs.slides) == 2
        finally:
            os.unlink(output_path)


class TestPPTXRendererColorTheme:
    """A color_theme choice (see app.design_system.COLOR_THEMES) must
    actually change the rendered title color on the no-template path -
    otherwise asking the user for a preference and then ignoring it would
    be worse than not asking at all."""

    def _title_slide(self):
        return [{
            "slideIndex": 0,
            "layoutId": "1",
            "layoutName": "Default",
            "contents": [{
                "type": "text",
                "placeholderIdx": 0,
                "runs": [{"text": "Title", "bold": True, "fontSizePt": 36}],
            }],
        }]

    def _render_and_get_title_run(self, color_theme):
        renderer = PPTXRenderer(template_path=None, color_theme=color_theme)
        with tempfile.NamedTemporaryFile(suffix=".pptx", delete=False) as f:
            output_path = f.name
        try:
            result = renderer.render(
                slides=self._title_slide(), metadata={"title": "T"}, output_path=output_path,
            )
            from pptx import Presentation
            prs = Presentation(result)
            slide = prs.slides[0]
            for shape in slide.shapes:
                if shape.has_text_frame and shape.text_frame.text == "Title":
                    return shape.text_frame.paragraphs[0].runs[0]
            raise AssertionError("title run not found")
        finally:
            os.unlink(output_path)

    def test_warm_theme_applies_the_warm_primary_color_to_the_title(self):
        run = self._render_and_get_title_run("warm")
        assert run.font.color.rgb == _hex_to_rgbcolor(next(c["value"] for c in COLOR_THEMES["warm"] if c["name"] == "primary"))

    def test_no_theme_defaults_to_the_professional_primary_color(self):
        run = self._render_and_get_title_run(None)
        assert run.font.color.rgb == _hex_to_rgbcolor(next(c["value"] for c in COLOR_THEMES["professional"] if c["name"] == "primary"))

    def test_unknown_theme_falls_back_to_professional(self):
        run = self._render_and_get_title_run("not-a-real-theme")
        assert run.font.color.rgb == _hex_to_rgbcolor(next(c["value"] for c in COLOR_THEMES["professional"] if c["name"] == "primary"))

    def test_different_themes_produce_different_title_colors(self):
        warm_run = self._render_and_get_title_run("warm")
        mono_run = self._render_and_get_title_run("monochrome")
        assert warm_run.font.color.rgb != mono_run.font.color.rgb


class TestPPTXRendererDefaultShapeSizing:
    """Regression: PositionConfig's fields are documented as inches (plain
    floats - see layout.py), but _add_text_box/_add_image used to pass them
    straight to python-pptx's add_textbox/add_picture, which interpret a
    bare number as EMU (1 inch = 914400 EMU). A "1.5 inch" position became a
    shape 1.5 EMU wide - sub-pixel, invisible - on every slide of the
    no-template render path. python-pptx (and this test) can still read the
    (garbage) values back, which is exactly why this shipped unnoticed:
    only an actual rendering engine (PowerPoint/LibreOffice) showed a blank
    slide. Confirmed by converting a rendered file with LibreOffice headless
    and finding zero extractable text before this fix, full text after.
    """

    def test_text_box_is_sized_in_inches_not_raw_emu(self):
        from pptx import Presentation
        from pptx.util import Inches

        renderer = PPTXRenderer(template_path=None)
        slides = [{
            "slideIndex": 0,
            "layoutId": "1",
            "layoutName": "Default",
            "contents": [{
                "type": "text",
                "placeholderIdx": 0,
                "runs": [{"text": "Title", "bold": True, "fontSizePt": 36}],
            }],
        }]
        with tempfile.NamedTemporaryFile(suffix=".pptx", delete=False) as f:
            output_path = f.name
        try:
            renderer.render(slides=slides, metadata={"title": "T"}, output_path=output_path)
            prs = Presentation(output_path)
            shape = next(s for s in prs.slides[0].shapes if s.has_text_frame and not s.is_placeholder)

            # The old bug produced shapes a handful of EMU wide/tall; a
            # correctly-converted inch-based size is always comfortably
            # above a tenth of an inch for any real slide element.
            assert shape.width > Inches(0.1)
            assert shape.height > Inches(0.1)
            # And matches what LayoutEngine.calculate_title_position actually
            # specified (1 inch margin either side on the default 10in slide).
            assert shape.width == Inches(8.0)
        finally:
            os.unlink(output_path)

    def test_multiple_stacked_content_blocks_use_inch_offsets(self):
        """The top_offset accumulator mixed a Length (EMU) with
        PositionConfig's inch-float on the second+ content block - fixed to
        stay in inches throughout."""
        from pptx import Presentation
        from pptx.util import Inches

        renderer = PPTXRenderer(template_path=None)
        slides = [{
            "slideIndex": 0,
            "layoutId": "1",
            "layoutName": "Default",
            "contents": [
                {"type": "text", "placeholderIdx": 0, "runs": [{"text": "First block"}]},
                {"type": "text", "placeholderIdx": 1, "runs": [{"text": "Second block"}]},
            ],
        }]
        with tempfile.NamedTemporaryFile(suffix=".pptx", delete=False) as f:
            output_path = f.name
        try:
            renderer.render(slides=slides, metadata={"title": "T"}, output_path=output_path)
            prs = Presentation(output_path)
            text_shapes = [s for s in prs.slides[0].shapes if s.has_text_frame and not s.is_placeholder]
            assert len(text_shapes) == 2
            for shape in text_shapes:
                assert shape.top < Inches(1000), "top offset leaked raw EMU into the inches field"
                assert shape.height > Inches(0.1)
        finally:
            os.unlink(output_path)