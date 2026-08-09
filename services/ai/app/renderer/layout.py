"""
Layout Engine

Provides intelligent slide layout positioning and spacing rules.
Works with both template-based and default rendering modes.
"""

from __future__ import annotations
from typing import Dict, Optional
from dataclasses import dataclass
from enum import Enum

from app.observability.logging import get_logger

logger = get_logger(__name__)


class LayoutType(Enum):
    """Slide layout types."""
    TITLE = "title"
    CONTENT = "content"
    TWO_COLUMN = "two_column"
    IMAGE = "image"
    TABLE = "table"
    CONCLUSION = "conclusion"


@dataclass
class LayoutConfig:
    """Configuration for a slide layout."""
    layout_type: LayoutType
    content_area_percent: float = 0.70  # 70% of slide for content
    margin_horizontal: float = 0.5  # inches
    margin_vertical: float = 0.4  # inches
    padding_between_sections: float = 0.2  # inches


@dataclass
class PositionConfig:
    """Positioning configuration for slide elements."""
    left: float  # inches
    top: float  # inches
    width: float  # inches
    height: float  # inches


class LayoutEngine:
    """
    Provides intelligent positioning logic for slide elements.

    Rules:
    - 60-70% content area, remaining for margins/white space
    - Proper spacing between elements
    - No stacking text elements
    - Readable font sizes (≥18pt for body, ≥32pt for titles)
    """

    # Slide dimensions (standard 16:9)
    SLIDE_WIDTH = 10.0  # inches
    SLIDE_HEIGHT = 5.625  # inches

    # Layout configurations
    LAYOUTS: Dict[LayoutType, LayoutConfig] = {
        LayoutType.TITLE: LayoutConfig(
            layout_type=LayoutType.TITLE,
            content_area_percent=0.65,
            margin_horizontal=1.0,
            margin_vertical=1.0,
            padding_between_sections=0.15,
        ),
        LayoutType.CONTENT: LayoutConfig(
            layout_type=LayoutType.CONTENT,
            content_area_percent=0.70,
            margin_horizontal=0.6,
            margin_vertical=0.6,
            padding_between_sections=0.2,
        ),
        LayoutType.TWO_COLUMN: LayoutConfig(
            layout_type=LayoutType.TWO_COLUMN,
            content_area_percent=0.70,
            margin_horizontal=0.6,
            margin_vertical=0.6,
            padding_between_sections=0.2,
        ),
        LayoutType.IMAGE: LayoutConfig(
            layout_type=LayoutType.IMAGE,
            content_area_percent=0.70,
            margin_horizontal=0.8,
            margin_vertical=0.6,
            padding_between_sections=0.2,
        ),
        LayoutType.TABLE: LayoutConfig(
            layout_type=LayoutType.TABLE,
            content_area_percent=0.70,
            margin_horizontal=0.6,
            margin_vertical=0.6,
            padding_between_sections=0.2,
        ),
        LayoutType.CONCLUSION: LayoutConfig(
            layout_type=LayoutType.CONCLUSION,
            content_area_percent=0.65,
            margin_horizontal=1.0,
            margin_vertical=1.0,
            padding_between_sections=0.15,
        ),
    }

    @classmethod
    def get_layout(cls, layout_type: LayoutType) -> LayoutConfig:
        """Get layout configuration by type."""
        return cls.LAYOUTS.get(layout_type, cls.LAYOUTS[LayoutType.CONTENT])

    @classmethod
    def calculate_title_position(cls, layout: LayoutConfig) -> PositionConfig:
        """
        Calculate title position and dimensions.

        Title is centered horizontally, positioned near the top.
        """
        width = cls.SLIDE_WIDTH - (2 * layout.margin_horizontal)
        height = 1.2  # fixed height for title area
        left = layout.margin_horizontal
        top = layout.margin_vertical

        return PositionConfig(left=left, top=top, width=width, height=height)

    @classmethod
    def calculate_content_position(
        cls,
        layout: LayoutConfig,
        has_title: bool = True,
        column: Optional[int] = None,  # 0 or 1 for two-column
    ) -> PositionConfig:
        """
        Calculate content position and dimensions.

        Args:
            layout: Layout configuration
            has_title: Whether slide has a title
            column: Optional column index for two-column layouts
        """
        # Calculate content area
        width = cls.SLIDE_WIDTH - (2 * layout.margin_horizontal)
        height = cls.SLIDE_HEIGHT - (2 * layout.margin_vertical)

        # Adjust for title
        if has_title:
            title_height = 1.2 + layout.padding_between_sections
            top = layout.margin_vertical + title_height
            height -= title_height
        else:
            top = layout.margin_vertical

        left = layout.margin_horizontal

        # Handle two-column layout
        if column is not None:
            width = width / 2
            if column == 1:
                left += width
            # Add gap between columns
            gap = 0.3
            width -= gap / 2
            if column == 0:
                width -= gap / 2
            elif column == 1:
                left += gap / 2

        return PositionConfig(left=left, top=top, width=width, height=height)

    @classmethod
    def calculate_image_position(
        cls,
        layout: LayoutConfig,
        has_title: bool = True,
        width_percent: float = 0.4,  # image width as % of content area
    ) -> PositionConfig:
        """
        Calculate image position.

        Images are typically positioned on the left or right side.
        """
        content_pos = cls.calculate_content_position(layout, has_title)

        image_width = content_pos.width * width_percent
        image_height = content_pos.height * 0.8

        left = content_pos.left
        top = content_pos.top + (content_pos.height - image_height) / 2

        return PositionConfig(left=left, top=top, width=image_width, height=image_height)

    @classmethod
    def calculate_table_position(
        cls,
        layout: LayoutConfig,
        has_title: bool = True,
    ) -> PositionConfig:
        """
        Calculate table position.

        Tables span the full content width.
        """
        return cls.calculate_content_position(layout, has_title)

    @classmethod
    def infer_layout_type(cls, slide_data: Dict) -> LayoutType:
        """
        Infer layout type from slide content.

        Analyzes slide contents to determine the most appropriate layout.
        """
        contents = slide_data.get("contents", [])

        has_image = any(c.get("type") == "image" for c in contents)
        has_table = any(c.get("type") == "table" for c in contents)
        has_chart = any(c.get("type") == "chart" for c in contents)

        text_items = [c for c in contents if c.get("type") == "text"]

        # Object slides are classified by what they carry, before any
        # text-shape reasoning - a slide whose point is a table or a chart
        # needs that layout even though it also has a title and some prose.
        if has_table:
            return LayoutType.TABLE

        # Chart and image both use the same generous content-area layout -
        # neither maps to a distinct LayoutType of its own (see LayoutType),
        # so a chart falls through to CONTENT below, same as it did before
        # charts were a real content type.
        if has_image:
            return LayoutType.IMAGE

        if has_chart:
            return LayoutType.CONTENT

        # A genuine title slide carries a title and, at most, a one-line
        # subtitle - no bulleted body.
        #
        # This used to be `len(contents) <= 2`, which matched the *ordinary*
        # shape the content designer emits for every content slide (one title
        # text item + one body text item). The result was that essentially
        # every slide in a deck was classified TITLE, rendered on the title
        # layout, and had its bullets stranded outside the layout's
        # placeholders. Judge by whether the body actually reads like a body.
        if not text_items:
            return LayoutType.TITLE

        body_items = text_items[1:]
        body_runs = [r for c in body_items for r in c.get("runs", [])]
        body_is_bulleted = any(r.get("bullet") for r in body_runs)
        if len(body_runs) <= 1 and not body_is_bulleted:
            return LayoutType.TITLE

        # Two-column: a title plus two or more distinct body text items.
        if len(body_items) >= 2:
            return LayoutType.TWO_COLUMN

        # Default content
        return LayoutType.CONTENT