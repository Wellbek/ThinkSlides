"""
Default Template Generator

Creates a built-in default PPTX template with proper layouts.
This ensures good-looking slides even without user-provided templates.
"""

from __future__ import annotations
import os
import tempfile
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.enum.shapes import MSO_SHAPE
from pptx.dml.color import RGBColor

from app.observability.logging import get_logger

logger = get_logger(__name__)


class DefaultTemplateGenerator:
    """Generates a built-in default PPTX template."""

    SLIDE_WIDTH = Inches(10)
    SLIDE_HEIGHT = Inches(5.625)

    # Theme colors
    COLORS = {
        "primary": RGBColor(31, 78, 120),  # Professional blue
        "secondary": RGBColor(41, 128, 185),  # Lighter blue
        "accent": RGBColor(255, 127, 80),  # Orange accent
        "dark": RGBColor(21, 51, 80),  # Dark blue
        "light": RGBColor(240, 244, 248),  # Light gray
        "white": RGBColor(255, 255, 255),
        "gray": RGBColor(128, 128, 128),
    }

    @classmethod
    def generate(cls, output_path: str | None = None) -> str:
        """
        Generate a default template PPTX file.

        Args:
            output_path: Optional path to save the template

        Returns:
            Path to the generated template file
        """
        prs = Presentation()
        prs.slide_width = cls.SLIDE_WIDTH
        prs.slide_height = cls.SLIDE_HEIGHT

        # Create layouts
        cls._add_title_layout(prs)
        cls._add_content_layout(prs)
        cls._add_two_column_layout(prs)
        cls._add_image_layout(prs)
        cls._add_table_layout(prs)
        cls._add_conclusion_layout(prs)

        # Set up master slide styling
        cls._apply_master_styling(prs)

        if output_path is None:
            output_path = os.path.join(
                tempfile.gettempdir(),
                "thinkslides_default_template.pptx"
            )

        prs.save(output_path)
        logger.info("default_template_generated", path=output_path)

        return output_path

    @classmethod
    def _apply_master_styling(cls, prs: Presentation) -> None:
        """Apply styling to the master slide."""
        if not prs.slide_masters:
            return

        master = prs.slide_masters[0]
        background = master.background
        fill = background.fill
        fill.solid()
        fill.fore_color.rgb = cls.COLORS["white"]

        # Remove default text boxes from master
        for shape in list(master.shapes):
            if shape.has_text_frame:
                master.shapes._spTree.remove(shape._element)

    @classmethod
    def _add_title_layout(cls, prs: Presentation) -> None:
        """Add a title slide layout."""
        layout = prs.slide_layouts[0]  # Blank layout
        slide = prs.slides.add_slide(layout)

        # Add title placeholder
        title_box = slide.shapes.add_textbox(
            Inches(1.0), Inches(1.5), Inches(8.0), Inches(1.5)
        )
        tf = title_box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = "Slide Title"
        p.font.size = Pt(36)
        p.font.bold = True
        p.font.color.rgb = cls.COLORS["primary"]

        # Add subtitle placeholder
        subtitle_box = slide.shapes.add_textbox(
            Inches(1.0), Inches(3.0), Inches(8.0), Inches(1.0)
        )
        tf = subtitle_box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = "Subtitle goes here"
        p.font.size = Pt(20)
        p.font.color.rgb = cls.COLORS["gray"]

        # Delete slide (we only want the layout)
        rId = prs.slides._sldIdLst[prs.slides.index(slide)].rId
        del prs.slides._sldIdLst[prs.slides.index(slide)]

    @classmethod
    def _add_content_layout(cls, prs: Presentation) -> None:
        """Add a content slide layout."""
        layout = prs.slide_layouts[0]
        slide = prs.slides.add_slide(layout)

        # Add title placeholder
        title_box = slide.shapes.add_textbox(
            Inches(0.6), Inches(0.4), Inches(9.0), Inches(1.0)
        )
        tf = title_box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = "Content Title"
        p.font.size = Pt(32)
        p.font.bold = True
        p.font.color.rgb = cls.COLORS["dark"]

        # Add content placeholder
        content_box = slide.shapes.add_textbox(
            Inches(0.6), Inches(1.5), Inches(9.0), Inches(3.5)
        )
        tf = content_box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = "• Content point 1\n• Content point 2\n• Content point 3"
        p.font.size = Pt(20)
        p.font.color.rgb = cls.COLORS["dark"]
        p.space_after = Pt(12)

        # Delete slide
        rId = prs.slides._sldIdLst[prs.slides.index(slide)].rId
        del prs.slides._sldIdLst[prs.slides.index(slide)]

    @classmethod
    def _add_two_column_layout(cls, prs: Presentation) -> None:
        """Add a two-column layout."""
        layout = prs.slide_layouts[0]
        slide = prs.slides.add_slide(layout)

        # Title placeholder
        title_box = slide.shapes.add_textbox(
            Inches(0.6), Inches(0.4), Inches(9.0), Inches(1.0)
        )
        tf = title_box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = "Two Column Title"
        p.font.size = Pt(32)
        p.font.bold = True
        p.font.color.rgb = cls.COLORS["dark"]

        # Left column placeholder
        left_box = slide.shapes.add_textbox(
            Inches(0.6), Inches(1.5), Inches(4.0), Inches(3.5)
        )
        tf = left_box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = "• Column 1 point 1\n• Column 1 point 2"
        p.font.size = Pt(20)
        p.font.color.rgb = cls.COLORS["dark"]

        # Right column placeholder
        right_box = slide.shapes.add_textbox(
            Inches(5.0), Inches(1.5), Inches(4.0), Inches(3.5)
        )
        tf = right_box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = "• Column 2 point 1\n• Column 2 point 2"
        p.font.size = Pt(20)
        p.font.color.rgb = cls.COLORS["dark"]

        # Delete slide
        rId = prs.slides._sldIdLst[prs.slides.index(slide)].rId
        del prs.slides._sldIdLst[prs.slides.index(slide)]

    @classmethod
    def _add_image_layout(cls, prs: Presentation) -> None:
        """Add an image-focused layout."""
        layout = prs.slide_layouts[0]
        slide = prs.slides.add_slide(layout)

        # Title placeholder
        title_box = slide.shapes.add_textbox(
            Inches(0.6), Inches(0.4), Inches(9.0), Inches(1.0)
        )
        tf = title_box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = "Image Title"
        p.font.size = Pt(32)
        p.font.bold = True
        p.font.color.rgb = cls.COLORS["dark"]

        # Image placeholder (rectangle shape)
        img_placeholder = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE,
            Inches(1.0), Inches(1.5), Inches(4.0), Inches(3.0)
        )
        img_placeholder.fill.solid()
        img_placeholder.fill.fore_color.rgb = cls.COLORS["light"]
        img_placeholder.line.color.rgb = cls.COLORS["gray"]

        # Content placeholder
        content_box = slide.shapes.add_textbox(
            Inches(5.5), Inches(1.5), Inches(4.0), Inches(3.5)
        )
        tf = content_box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = "• Image description\n• Key points about the image\n• Supporting text"
        p.font.size = Pt(20)
        p.font.color.rgb = cls.COLORS["dark"]

        # Delete slide
        rId = prs.slides._sldIdLst[prs.slides.index(slide)].rId
        del prs.slides._sldIdLst[prs.slides.index(slide)]

    @classmethod
    def _add_table_layout(cls, prs: Presentation) -> None:
        """Add a table-focused layout."""
        layout = prs.slide_layouts[0]
        slide = prs.slides.add_slide(layout)

        # Title placeholder
        title_box = slide.shapes.add_textbox(
            Inches(0.6), Inches(0.4), Inches(9.0), Inches(1.0)
        )
        tf = title_box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = "Table Title"
        p.font.size = Pt(32)
        p.font.bold = True
        p.font.color.rgb = cls.COLORS["dark"]

        # Table placeholder
        table_placeholder = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE,
            Inches(0.6), Inches(1.5), Inches(9.0), Inches(3.5)
        )
        table_placeholder.fill.solid()
        table_placeholder.fill.fore_color.rgb = cls.COLORS["light"]
        table_placeholder.line.color.rgb = cls.COLORS["gray"]

        # Delete slide
        rId = prs.slides._sldIdLst[prs.slides.index(slide)].rId
        del prs.slides._sldIdLst[prs.slides.index(slide)]

    @classmethod
    def _add_conclusion_layout(cls, prs: Presentation) -> None:
        """Add a conclusion slide layout."""
        layout = prs.slide_layouts[0]
        slide = prs.slides.add_slide(layout)

        # Main content centered
        content_box = slide.shapes.add_textbox(
            Inches(1.0), Inches(1.5), Inches(8.0), Inches(3.0)
        )
        tf = content_box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = "Thank You"
        p.font.size = Pt(36)
        p.font.bold = True
        p.alignment = 1  # Center

        p2 = tf.add_paragraph()
        p2.text = "Questions?"
        p2.font.size = Pt(28)
        p2.alignment = 1

        # Delete slide
        rId = prs.slides._sldIdLst[prs.slides.index(slide)].rId
        del prs.slides._sldIdLst[prs.slides.index(slide)]


def get_default_template_path(output_dir: str = "/app/templates") -> str:
    """
    Get or generate the default template.

    Args:
        output_dir: Directory to store the template

    Returns:
        Path to the default template file
    """
    os.makedirs(output_dir, exist_ok=True)
    template_path = os.path.join(output_dir, "default_template.pptx")

    # Generate if not exists
    if not os.path.exists(template_path):
        template_path = DefaultTemplateGenerator.generate(template_path)

    return template_path