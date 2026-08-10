"""
Typography Engine

Provides font sizing, spacing, and contrast rules for slide text.
Ensures readability and visual hierarchy.
"""

from __future__ import annotations
from typing import Dict, Optional
from dataclasses import dataclass
from enum import Enum

from pptx.util import Pt
from app.observability.logging import get_logger

logger = get_logger(__name__)


class FontWeight(Enum):
    """Font weight options."""
    REGULAR = "regular"
    BOLD = "bold"


class FontSize(Enum):
    """Standard font sizes for slides."""
    TITLE_LARGE = Pt(44)
    TITLE = Pt(36)
    SUBTITLE = Pt(32)
    HEADING_1 = Pt(28)
    HEADING_2 = Pt(24)
    BODY = Pt(20)
    BODY_SMALL = Pt(18)
    CAPTION = Pt(14)
    FOOTNOTE = Pt(12)


@dataclass
class TypographyConfig:
    """Typography configuration."""
    title_font_name: str = "Arial"
    body_font_name: str = "Arial"
    title_size: int = 36
    body_size: int = 20
    line_spacing: float = 1.2
    paragraph_spacing: float = 0.3  # in line units


@dataclass
class TextStyle:
    """Style for a text run."""
    font_name: str
    font_size_pt: int
    bold: bool = False
    italic: bool = False
    underline: bool = False
    color_rgb: Optional[tuple[int, int, int]] = None


# Legibility floors, applied after the design system's own sizes. A template
# that specifies 11pt body copy is describing a document, not a slide.
MIN_TITLE_PT = 28
MIN_BODY_PT = 16

# Sub-bullets step down from the body size by these ratios. Two levels is as
# deep as slide copy should ever go; anything further reuses level 2.
_LEVEL_RATIOS = (1.0, 0.85, 0.75)


@dataclass(frozen=True)
class TypeScale:
    """
    The concrete point sizes and typefaces one deck renders in.

    Resolved from the run's design system - an uploaded template's fonts, or
    the built-in default's. ``TypographyEngine``'s class methods return a
    hardcoded Arial 36/20/18 regardless of what was extracted, so a template
    whose fonts had been read, published to the model and used to write the
    copy still rendered in whatever the base template's theme font was. This
    is the type half of that: the renderer asks a TypeScale, not the engine.
    """
    title_font: str = "Arial"
    body_font: str = "Arial"
    title_size_pt: float = 36.0
    body_size_pt: float = 20.0
    caption_size_pt: float = 12.0
    title_bold: bool = True

    @classmethod
    def from_design_system(cls, design_system: dict | None) -> "TypeScale":
        typography = ((design_system or {}).get("typography") or {})
        title = typography.get("titleFont") or {}
        body = typography.get("bodyFont") or {}
        caption = typography.get("captionFont") or {}

        defaults = cls()
        title_font = str(title.get("typeface") or defaults.title_font)
        body_font = str(body.get("typeface") or title_font)

        return cls(
            title_font=title_font,
            body_font=body_font,
            title_size_pt=_positive(title.get("sizePt"), defaults.title_size_pt),
            body_size_pt=_positive(body.get("sizePt"), defaults.body_size_pt),
            caption_size_pt=_positive(caption.get("sizePt"), defaults.caption_size_pt),
            title_bold=bool(title.get("bold", defaults.title_bold)),
        )

    def title_style(self, hero: bool = False) -> TextStyle:
        """Title styling. ``hero`` is the cover/section/closing treatment,
        which steps up a size because it is the only thing on the slide."""
        size = self.title_size_pt * (1.15 if hero else 1.0)
        return TextStyle(
            font_name=self.title_font,
            font_size_pt=max(MIN_TITLE_PT, round(size)),
            bold=self.title_bold,
        )

    def body_style(self, level: int = 0) -> TextStyle:
        ratio = _LEVEL_RATIOS[min(max(int(level or 0), 0), len(_LEVEL_RATIOS) - 1)]
        return TextStyle(
            font_name=self.body_font,
            font_size_pt=max(MIN_BODY_PT, round(self.body_size_pt * ratio)),
            bold=False,
        )


def _positive(value, fallback: float) -> float:
    """A usable point size from a design system field, or the fallback."""
    try:
        size = float(value)
    except (TypeError, ValueError):
        return fallback
    return size if size > 0 else fallback


class TypographyEngine:
    """
    Provides typography rules and utilities.

    Rules:
    - Title font size ≥32pt
    - Body font size ≥18pt
    - Line spacing 1.1-1.3
    - Proper contrast (light on dark, dark on light)
    """

    # WCAG AA color contrast ratios
    # minimum contrast ratio: 4.5:1 for normal text
    COLOR_PALETTE: Dict[str, tuple[int, int, int]] = {
        "black": (0, 0, 0),
        "dark_gray": (64, 64, 64),
        "gray": (128, 128, 128),
        "light_gray": (192, 192, 192),
        "white": (255, 255, 255),
        "primary_blue": (31, 78, 120),  # professional blue
        "primary_dark": (21, 51, 80),
        "accent": (255, 127, 80),  # orange accent
        "success": (76, 175, 80),
        "warning": (255, 152, 0),
        "error": (244, 67, 54),
    }

    FONT_STACK: Dict[str, list[str]] = {
        "sans": ["Arial", "Helvetica", "Calibri", "Verdana", "sans-serif"],
        "serif": ["Georgia", "Times New Roman", "Palatino", "serif"],
        "mono": ["Consolas", "Courier New", "monospace"],
    }

    @classmethod
    def get_font_stack(cls, style: str = "sans") -> list[str]:
        """Get font stack by style."""
        return cls.FONT_STACK.get(style, cls.FONT_STACK["sans"])

    @classmethod
    def get_title_style(cls, level: int = 1) -> TextStyle:
        """
        Get title style based on heading level.

        Args:
            level: Heading level (1=highest, 2=subtitle, 3=section)
        """
        sizes = {
            1: FontSize.TITLE.value,
            2: FontSize.SUBTITLE.value,
            3: FontSize.HEADING_1.value,
        }

        size_pt = sizes.get(level, FontSize.HEADING_1.value).pt

        return TextStyle(
            font_name="Arial",
            font_size_pt=size_pt,
            bold=True,
        )

    @classmethod
    def get_body_style(cls, level: int = 0) -> TextStyle:
        """
        Get body text style based on bullet level.

        Args:
            level: Bullet nesting level (0=top level, 1=sub-bullet, etc.)
        """
        sizes = {
            0: FontSize.BODY.value,
            1: FontSize.BODY_SMALL.value,
            2: FontSize.CAPTION.value,
        }

        size_pt = sizes.get(level, FontSize.BODY_SMALL.value).pt

        return TextStyle(
            font_name="Arial",
            font_size_pt=size_pt,
            bold=False,
        )

    @classmethod
    def validate_font_size(cls, size_pt: int, is_title: bool = False) -> int:
        """
        Validate and adjust font size to meet minimum requirements.

        Args:
            size_pt: Requested font size in points
            is_title: Whether this is title text

        Returns:
            Adjusted font size meeting minimum requirements
        """
        min_size = 32 if is_title else 18

        if size_pt < min_size:
            logger.warning(
                "font_size_adjusted",
                requested=size_pt,
                adjusted=min_size,
                is_title=is_title,
            )
            return min_size

        return size_pt

    @classmethod
    def ensure_contrast(
        cls,
        text_color: tuple[int, int, int],
        background_color: tuple[int, int, int],
    ) -> tuple[int, int, int]:
        """
        Ensure sufficient contrast between text and background.

        If contrast ratio is below WCAG AA (4.5:1), adjusts text color.

        Args:
            text_color: RGB tuple for text color
            background_color: RGB tuple for background

        Returns:
            Adjusted text color with sufficient contrast
        """
        contrast_ratio = cls._calculate_contrast(text_color, background_color)

        if contrast_ratio >= 4.5:
            return text_color

        # Adjust to nearest high-contrast color
        background_luma = cls._calculate_luminance(background_color)

        if background_luma > 0.5:
            # Light background, use dark text
            return cls.COLOR_PALETTE["dark_gray"]
        else:
            # Dark background, use light text
            return cls.COLOR_PALETTE["white"]

    @classmethod
    def _calculate_contrast(
        cls,
        color1: tuple[int, int, int],
        color2: tuple[int, int, int],
    ) -> float:
        """
        Calculate contrast ratio between two colors.

        Returns:
            Contrast ratio (higher = more contrast)
        """
        l1 = cls._calculate_luminance(color1)
        l2 = cls._calculate_luminance(color2)

        lighter = max(l1, l2)
        darker = min(l1, l2)

        return (lighter + 0.05) / (darker + 0.05)

    @classmethod
    def _calculate_luminance(cls, color: tuple[int, int, int]) -> float:
        """
        Calculate relative luminance of a color.

        Returns:
            Luminance value between 0 (black) and 1 (white)
        """
        r, g, b = [c / 255.0 for c in color]

        # Apply gamma correction
        def gamma_correct(channel: float) -> float:
            if channel <= 0.03928:
                return channel / 12.92
            return ((channel + 0.055) / 1.055) ** 2.4

        r = gamma_correct(r)
        g = gamma_correct(g)
        b = gamma_correct(b)

        # Calculate luminance
        return 0.2126 * r + 0.7152 * g + 0.0722 * b