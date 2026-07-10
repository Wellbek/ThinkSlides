"""
Default Template Generator

Creates a built-in default PPTX template used as a renderer fallback when the
user has not provided one. The template only needs to supply valid slide
layouts and dimensions; slide content is added per-presentation by the
renderer.

The design tokens (slide dimensions and theme palette) are NOT hardcoded
here. They are derived from ``app.design_system.get_default_design_system()``,
which is the same dict surfaced to the LLM via the
``extract_template_design_system`` tool. This makes that dict the single
source of truth, so the LLM's design tokens and the rendered output can never
drift apart (issue #24).

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
from pptx.util import Emu
from pptx.dml.color import RGBColor

from app.design_system import get_default_design_system
from app.config import settings
from app.observability.logging import get_logger

logger = get_logger(__name__)


def _hex_to_rgbcolor(hex_value: str) -> RGBColor:
    """Convert a ``#RRGGBB`` hex string to a python-pptx ``RGBColor``."""
    h = (hex_value or "").lstrip("#")
    if len(h) != 6:
        raise ValueError(f"Invalid hex color: {hex_value!r}")
    return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


class DefaultTemplateGenerator:
    """Generates a built-in default PPTX template.

    Dimensions and palette are derived from the shared default design system
    (see ``app.design_system``) so the rendered fallback always matches the
    tokens the LLM was given.
    """

    @classmethod
    def _design_system(cls) -> dict:
        return get_default_design_system()

    @classmethod
    def _palette_rgb(cls) -> dict[str, RGBColor]:
        """Return the default palette as ``{name: RGBColor}``.

        Colors that fail to parse are skipped (with a warning) rather than
        aborting template generation.
        """
        colors: dict[str, RGBColor] = {}
        for entry in cls._design_system()["palette"]["colors"]:
            name = entry.get("name", "")
            try:
                colors[name] = _hex_to_rgbcolor(entry["value"])
            except (ValueError, KeyError) as e:
                logger.warning("default_palette_color_skipped", name=name, error=str(e))
        return colors

    @classmethod
    def _dimensions(cls) -> tuple[int, int]:
        """Return ``(width_emu, height_emu)`` from the default design system."""
        dims = cls._design_system()["slideDimensions"]
        return int(dims["widthEmu"]), int(dims["heightEmu"])

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
        width_emu, height_emu = cls._dimensions()
        prs.slide_width = Emu(width_emu)
        prs.slide_height = Emu(height_emu)

        # White master background; remove default master text placeholders so
        # layouts render cleanly. The white comes from the shared palette so it
        # stays in sync with the design system.
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
        """Apply the palette's white background to the master slide."""
        if not prs.slide_masters:
            return

        colors = cls._palette_rgb()
        white = colors.get("white")
        if white is None:
            # Should never happen with the built-in palette, but stay robust.
            white = RGBColor(255, 255, 255)

        master = prs.slide_masters[0]
        background = master.background
        fill = background.fill
        fill.solid()
        fill.fore_color.rgb = white


def get_default_template_path(output_dir: str | None = None) -> str:
    """
    Get or generate the default template.

    Args:
        output_dir: Directory to store the template. Defaults to
            ``settings.template_dir`` so the location is configurable like the
            other storage dirs (rather than a hardcoded ``/app/templates``).

    Returns:
        Path to the default template file.
    """
    if output_dir is None:
        output_dir = settings.template_dir
    os.makedirs(output_dir, exist_ok=True)
    template_path = os.path.join(output_dir, "default_template.pptx")

    # Generate if not exists
    if not os.path.exists(template_path):
        template_path = DefaultTemplateGenerator.generate(template_path)

    return template_path
