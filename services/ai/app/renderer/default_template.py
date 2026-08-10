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
from pptx.oxml.ns import qn

from app.design_system import get_default_design_system
from app.config import settings
from app.observability.logging import get_logger

logger = get_logger(__name__)

# Bump whenever the generated template's geometry or styling changes, so
# deployments holding a previously-generated copy pick the new one up. The
# file is derived from code but cached as data on a persistent volume, so
# without this a fix here never reaches an environment that already has one.
#   v2 - rescale master/layout placeholders when moving the canvas to 16:9
#   v3 - canvas moved to PowerPoint's own 13.333 x 7.5in 16:9 page
DEFAULT_TEMPLATE_VERSION = 3


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

        # python-pptx's built-in template is 4:3 (10in x 7.5in) and its
        # master/layout placeholders are positioned for that canvas. Resizing
        # the canvas alone leaves every placeholder at its original geometry,
        # so on a 16:9 slide the body placeholders hang off the bottom edge -
        # e.g. "Title and Content"'s body ran to 6.70in on a 5.625in slide,
        # pushing bullets past the bottom of the rendered page. Rescale the
        # placeholder geometry by the same factor as the canvas.
        cls._rescale_placeholders(
            prs,
            x_scale=width_emu / prs.slide_width if prs.slide_width else 1.0,
            y_scale=height_emu / prs.slide_height if prs.slide_height else 1.0,
        )

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

    @staticmethod
    def _has_own_geometry(shape) -> bool:
        """True when a shape carries its own ``<a:xfrm>`` rather than inheriting."""
        spPr = shape._element.find(qn("p:spPr"))
        return spPr is not None and spPr.find(qn("a:xfrm")) is not None

    @classmethod
    def _rescale_placeholders(cls, prs: Presentation, x_scale: float, y_scale: float) -> None:
        """
        Scale master/layout placeholder geometry by the given factors.

        Called before the slide canvas is resized, while ``prs.slide_width``
        and ``prs.slide_height`` still describe the geometry the placeholders
        were authored against.

        Two subtleties, both of which silently corrupt the geometry if missed:

        * Most layout placeholders carry no ``<a:xfrm>`` and inherit position
          from the master. Assigning to them would freeze the inherited value
          as an explicit one, so they are skipped - scaling the master alone
          already moves them.
        * All four values must be read before any is written. Setting ``left``
          creates an ``<a:xfrm>`` whose other components default to zero, so a
          read-modify-write interleaved across the four attributes collapses
          top/width/height to 0.
        """
        if x_scale == 1.0 and y_scale == 1.0:
            return

        for master in prs.slide_masters:
            for container in (master, *master.slide_layouts):
                for shape in container.shapes:
                    if container is not master and not cls._has_own_geometry(shape):
                        continue

                    left, top = shape.left, shape.top
                    width, height = shape.width, shape.height

                    if left is not None:
                        shape.left = Emu(int(left * x_scale))
                    if top is not None:
                        shape.top = Emu(int(top * y_scale))
                    if width is not None:
                        shape.width = Emu(int(width * x_scale))
                    if height is not None:
                        shape.height = Emu(int(height * y_scale))

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

    The filename carries :data:`DEFAULT_TEMPLATE_VERSION`, so changing the
    generator produces a new file rather than silently reusing the old one.
    This is not cosmetic: the template lives on a persistent volume, and the
    previous unversioned name meant a deployed fix to the layout geometry
    never took effect on any environment that had generated a template before
    - it kept serving a months-old file whose body placeholder hung 77pt below
    the bottom of the slide.

    Args:
        output_dir: Directory to store the template. Defaults to
            ``settings.template_dir`` so the location is configurable like the
            other storage dirs (rather than a hardcoded ``/app/templates``).

    Returns:
        Path to the generated template file.
    """
    if output_dir is None:
        output_dir = settings.template_dir
    os.makedirs(output_dir, exist_ok=True)
    template_path = os.path.join(
        output_dir, f"default_template_v{DEFAULT_TEMPLATE_VERSION}.pptx"
    )

    if not os.path.exists(template_path):
        template_path = DefaultTemplateGenerator.generate(template_path)
        _remove_superseded_templates(output_dir, keep=os.path.basename(template_path))

    return template_path


def _remove_superseded_templates(output_dir: str, keep: str) -> None:
    """Delete default templates left behind by earlier versions."""
    try:
        stale = [
            name for name in os.listdir(output_dir)
            if name != keep
            and name.startswith("default_template")
            and name.endswith(".pptx")
        ]
    except OSError as e:
        logger.warning("default_template_cleanup_skipped", error=str(e))
        return

    for name in stale:
        try:
            os.remove(os.path.join(output_dir, name))
            logger.info("default_template_superseded_removed", name=name)
        except OSError as e:
            # A stale file left behind is harmless - it is simply never read.
            logger.warning("default_template_cleanup_failed", name=name, error=str(e))
