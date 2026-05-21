"""
Python Pydantic schemas mirroring the TypeScript slide schema.
Used by the AI service for validation and serialization.
"""

from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Optional, Literal, Union
from enum import Enum


class SlideDimensions(BaseModel):
    width_emu: int = Field(..., description="Width in EMU (914400 per inch)")
    height_emu: int = Field(..., description="Height in EMU")
    width_px: int
    height_px: int


class ColorEntry(BaseModel):
    name: str
    value: str  # hex "#1F4E79" or scheme "dk1"
    type: Literal["hex", "scheme", "system"] = "hex"


class Palette(BaseModel):
    colors: list[ColorEntry]
    scheme_name: str


class FontSpec(BaseModel):
    typeface: str
    size_pt: float
    bold: bool = False
    italic: bool = False
    color: Optional[str] = None


class Typography(BaseModel):
    title_font: FontSpec
    body_font: FontSpec
    caption_font: FontSpec
    custom_fonts: Optional[dict[str, FontSpec]] = None


class SpacingTokens(BaseModel):
    slide_padding_emu: int = 0
    element_gap_emu: int = 0
    title_top_margin_emu: int = 0
    body_top_margin_emu: int = 0
    corner_radius_emu: Optional[int] = None


class PlaceholderType(str, Enum):
    title = "title"
    body = "body"
    subtitle = "subtitle"
    ctr_title = "ctrTitle"
    sld_num = "sldNum"
    dt = "dt"
    ftr = "ftr"
    pic = "pic"
    chart = "chart"
    tbl = "tbl"
    custom = "custom"


class PlaceholderPosition(BaseModel):
    idx: int
    type: PlaceholderType
    name: str
    x_emu: int
    y_emu: int
    cx_emu: int
    cy_emu: int
    rotation: Optional[int] = None


class LayoutDef(BaseModel):
    layout_id: str
    layout_name: str
    master_id: str
    placeholders: list[PlaceholderPosition]
    has_background: bool = False
    has_decorations: bool = False


class MasterElement(BaseModel):
    element_type: Literal["shape", "image", "connector"]
    name: str
    position: dict  # x_emu, y_emu, cx_emu, cy_emu
    style_ref: Optional[str] = None


class EmbeddedMedia(BaseModel):
    media_type: Literal["image", "audio", "video"]
    r_id: str
    file_name: str
    mime_type: str
    file_size_bytes: Optional[int] = None


class DesignSystemBundle(BaseModel):
    palette: Palette
    typography: Typography
    spacing: SpacingTokens
    layouts: list[LayoutDef]
    slide_dimensions: SlideDimensions
    master_elements: list[MasterElement] = Field(default_factory=list)
    embedded_media: list[EmbeddedMedia] = Field(default_factory=list)


# ──────────────────────────────────────────────
# Slide Content Schema (agent output → renderer)
# ──────────────────────────────────────────────

class TextRun(BaseModel):
    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False
    font_size_pt: Optional[float] = None
    color_hex: Optional[str] = None
    font_name: Optional[str] = None
    bullet: bool = False
    level: int = 0


class ImageContent(BaseModel):
    source: Literal["url", "base64", "embedded"]
    data: str
    alt_text: Optional[str] = None
    width_emu: Optional[int] = None
    height_emu: Optional[int] = None
    placeholder_idx: Optional[int] = None


class TableContent(BaseModel):
    rows: list[list[str]]
    header_row: bool = False
    placeholder_idx: Optional[int] = None


class ChartContent(BaseModel):
    chart_type: Literal["bar", "line", "pie", "scatter"]
    title: Optional[str] = None
    categories: list[str]
    series: list[dict]  # name, values
    placeholder_idx: Optional[int] = None


class TextSlideContent(BaseModel):
    type: Literal["text"] = "text"
    placeholder_idx: int
    runs: list[TextRun]


class ImageSlideContent(BaseModel):
    type: Literal["image"] = "image"
    placeholder_idx: Optional[int] = None
    image: ImageContent


class TableSlideContent(BaseModel):
    type: Literal["table"] = "table"
    placeholder_idx: Optional[int] = None
    table: TableContent


class ChartSlideContent(BaseModel):
    type: Literal["chart"] = "chart"
    placeholder_idx: Optional[int] = None
    chart: ChartContent


SlideContentItem = Union[TextSlideContent, ImageSlideContent, TableSlideContent, ChartSlideContent]


class Slide(BaseModel):
    slide_index: int
    layout_id: str
    layout_name: str
    contents: list[SlideContentItem] = Field(default_factory=list)
    notes: Optional[str] = None
    speaker_notes: Optional[str] = None


class PresentationMetadata(BaseModel):
    title: str
    author: Optional[str] = None
    subject: Optional[str] = None
    created_at: str


class Presentation(BaseModel):
    template_id: Optional[str] = None
    design_system_bundle_id: Optional[str] = None
    dimensions: SlideDimensions
    slides: list[Slide]
    metadata: PresentationMetadata