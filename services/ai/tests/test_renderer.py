"""
Tests for the PPTX renderer.
"""

import pytest
import os
import tempfile
from app.renderer.pptx_renderer import PPTXRenderer


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