"""
Default Template Generator

Creates a built-in default PPTX template used as a renderer fallback when the
user has not provided one. The template only needs to supply valid slide
layouts and dimensions; slide content is added per-presentation by the
renderer.

Previous versions added throwaway slides (to preview styling) and then deleted
them by removing only the ``sldIdLst`` entry. That left the slide XML parts
and relationships orphaned in the package, so the saved PPTX contained
duplicate ``ppt/slides/slide1.xml`` zip entries. python-pptx opened it
leniently, but LibreOffice (used for preview rendering) rejected the corrupt
package with "source file could not be loaded", so no slide preview images
were ever generated.

This version adds no slides at all - it relies on python-pptx's built-in
layout catalog and only sets dimensions + master background - producing a
clean, valid PPTX that LibreOffice can render.
"""

from __future__ import annotations
import os
import tempfile
from pptx import Presentation
from pptx.util import Inches
from pptx.dml.color import RGBColor

from app.observability.logging import get_logger

logger = get_logger(__name__)


class DefaultTemplateGenerator:
    """Generates a built-in default PPTX template."""

    # 16:9 dimensions (10in x 5.625in)
    SLIDE_WIDTH = Inches(10)
    SLIDE_HEIGHT = Inches(5.625)

    # Theme colors (also surfaced to the LLM via get_default_design_system)
    COLORS = {
        "primary": RGBColor(31, 78, 120),    # professional blue
        "secondary": RGBColor(41, 128, 185),  # lighter blue
        "accent": RGBColor(255, 127, 80),     # orange accent
        "dark": RGBColor(21, 51, 80),         # dark blue
        "light": RGBColor(240, 244, 248),     # light gray
        "white": RGBColor(255, 255, 255),
        "gray": RGBColor(128, 128, 128),
    }

    @classmethod
    def generate(cls, output_path: str | None = None) -> str:
        """
        Generate a default template PPTX file.

        The template contains no slides - only the built-in slide layouts,
        16:9 dimensions, and a white master background. Slide content is
        added by the renderer when a presentation is generated.

        Args:
            output_path: Optional path to save the template.

        Returns:
            Path to the generated template file.
        """
        prs = Presentation()
        prs.slide_width = cls.SLIDE_WIDTH
        prs.slide_height = cls.SLIDE_HEIGHT

        # White master background; remove default master text placeholders so
        # layouts render cleanly.
        cls._apply_master_styling(prs)

        if output_path is None:
            output_path = os.path.join(
                tempfile.gettempdir(),
                "thinkslides_default_template.pptx",
            )

        prs.save(output_path)
        logger.info("default_template_generated", path=output_path)
        return output_path

    @classmethod
    def _apply_master_styling(cls, prs: Presentation) -> None:
        """Apply a white background to the master slide."""
        if not prs.slide_masters:
            return

        master = prs.slide_masters[0]
        background = master.background
        fill = background.fill
        fill.solid()
        fill.fore_color.rgb = cls.COLORS["white"]


def get_default_template_path(output_dir: str = "/app/templates") -> str:
    """
    Get or generate the default template.

    Args:
        output_dir: Directory to store the template.

    Returns:
        Path to the default template file.
    """
    os.makedirs(output_dir, exist_ok=True)
    template_path = os.path.join(output_dir, "default_template.pptx")

    # Generate if not exists
    if not os.path.exists(template_path):
        template_path = DefaultTemplateGenerator.generate(template_path)

    return template_path
