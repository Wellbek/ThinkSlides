"""
PPTX Renderer

Clones real layout XML from the template, fills placeholder elements
without touching rPr formatting attributes, and repacks.
Never recreates design from scratch — always clones and fills.
"""

from __future__ import annotations
import os
import re
import copy
import zipfile
import shutil
import tempfile
import xml.etree.ElementTree as ET
from typing import Optional
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.enum.text import MSO_AUTO_SIZE, PP_ALIGN
from pptx.enum.shapes import PP_PLACEHOLDER
from pptx.oxml.ns import qn
from pptx.oxml.xmlchemy import OxmlElement
from app.observability.logging import get_logger
from app.renderer.layout import LayoutEngine, LayoutType, PositionConfig
from app.renderer.typography import TypographyEngine
from app.renderer.default_template import get_default_template_path, _hex_to_rgbcolor
from app.renderer import text_fit
from app.renderer import table_style
from app.renderer import chart_renderer
from app.renderer.xml_utils import get_shape_position, emu_to_inches, inches_to_emu
from app.design_system import get_default_design_system

logger = get_logger(__name__)

# Cap on downloaded/decoded image size - guards against a runaway data: URI
# or a slow/oversized remote response inflating render time or memory.
MAX_IMAGE_BYTES = 8 * 1024 * 1024

_DATA_URI_RE = re.compile(r"^data:([\w/+.-]+);base64,(.*)$", re.DOTALL)

# Placeholders that hold the slide's title.
_TITLE_PLACEHOLDER_TYPES = frozenset({
    PP_PLACEHOLDER.TITLE,
    PP_PLACEHOLDER.CENTER_TITLE,
    PP_PLACEHOLDER.VERTICAL_TITLE,
})
# Chrome placeholders that must never receive slide content, and are left
# alone by the unfilled-placeholder sweep (they are empty by design).
# python-pptx does not normally clone these onto a slide, so this is a guard
# for templates that carry them explicitly.
_SKIPPED_PLACEHOLDER_TYPES = frozenset({
    PP_PLACEHOLDER.DATE,
    PP_PLACEHOLDER.FOOTER,
    PP_PLACEHOLDER.SLIDE_NUMBER,
})

# Standard PowerPoint body-bullet geometry: a 0.25" hanging indent per level.
BULLET_INDENT_EMU = 228600
# Glyph per outline level, matching PowerPoint's own default body scheme.
_BULLET_CHARS = {0: "•", 1: "–", 2: "▪"}
# Children that must FOLLOW the bullet definition inside <a:pPr>, per the
# DrawingML schema's fixed child order (lnSpc, spc*, buClr, buSz, buFont,
# buChar/buNone, tabLst, defRPr, extLst). Appending blindly would put bullet
# elements after defRPr and produce a file PowerPoint refuses to open.
_PPR_TAIL_TAGS = ("a:tabLst", "a:defRPr", "a:extLst")
# Bullet definitions to clear before re-declaring, so the helper is idempotent.
_BULLET_TAGS = ("a:buClrTx", "a:buClr", "a:buSzTx", "a:buSzPct", "a:buSzPts",
                "a:buFontTx", "a:buFont", "a:buNone", "a:buAutoNum", "a:buChar")


def _insert_into_pPr(pPr, element) -> None:
    """Insert ``element`` into ``pPr`` at a schema-valid position."""
    for tag in _PPR_TAIL_TAGS:
        existing = pPr.find(qn(tag))
        if existing is not None:
            existing.addprevious(element)
            return
    pPr.append(element)


def _apply_bullet_formatting(
    paragraph,
    bulleted: bool,
    level: int,
    color_hex: Optional[str] = None,
) -> None:
    """
    Write an explicit bullet definition onto a paragraph's ``<a:pPr>``.

    python-pptx's ``paragraph.level`` only sets the *indent* level - it does
    not add a bullet glyph. A free-standing text box (unlike a body
    placeholder) also inherits no bullet definition from the slide master, so
    content marked ``bullet: true`` previously rendered as unadorned lines of
    text with no marker at all. Emitting ``<a:buFont>``/``<a:buChar>`` (or
    ``<a:buNone>``) directly is the only way to make the bullet appear.

    ``color_hex`` tints the marker with the deck's brand color, so bullets on
    the no-template path still read as part of the design system rather than
    defaulting to body black.
    """
    pPr = paragraph._p.get_or_add_pPr()
    for tag in _BULLET_TAGS:
        for el in pPr.findall(qn(tag)):
            pPr.remove(el)

    if not bulleted:
        # Explicit, rather than merely absent: a placeholder-backed paragraph
        # would otherwise inherit the master's bullet for a line (a title, a
        # subtitle) that must not have one.
        _insert_into_pPr(pPr, OxmlElement("a:buNone"))
        return

    pPr.set("marL", str(BULLET_INDENT_EMU * (level + 1)))
    pPr.set("indent", str(-BULLET_INDENT_EMU))

    normalized = (color_hex or "").lstrip("#")
    if len(normalized) == 6:
        buClr = OxmlElement("a:buClr")
        srgbClr = OxmlElement("a:srgbClr")
        srgbClr.set("val", normalized.upper())
        buClr.append(srgbClr)
        _insert_into_pPr(pPr, buClr)

    buFont = OxmlElement("a:buFont")
    buFont.set("typeface", "Arial")
    buFont.set("panose", "020B0604020202020204")
    buFont.set("pitchFamily", "34")
    buFont.set("charset", "0")
    _insert_into_pPr(pPr, buFont)

    buChar = OxmlElement("a:buChar")
    buChar.set("char", _BULLET_CHARS.get(level, _BULLET_CHARS[0]))
    _insert_into_pPr(pPr, buChar)


def _resolve_image_bytes(img_data: dict) -> Optional[bytes]:
    """
    Decode/fetch the raw bytes for an ImageContent regardless of its
    `source` (base64 data, a bare/data-URI base64 string, or a remote URL).
    Returns None (never raises) on anything that isn't a genuine, size-capped
    image - callers treat that as "skip this image" rather than fail the
    whole render.
    """
    source = img_data.get("source")
    raw = img_data.get("data", "") or ""

    if source == "base64":
        m = _DATA_URI_RE.match(raw)
        if m:
            raw = m.group(2)
        try:
            import base64
            decoded = base64.b64decode(raw, validate=False)
        except Exception:
            return None
        if len(decoded) > MAX_IMAGE_BYTES:
            return None
        return decoded

    if source == "url":
        if not raw.startswith(("http://", "https://")):
            return None
        try:
            import httpx
            with httpx.Client(timeout=8.0, follow_redirects=True) as client:
                resp = client.get(raw)
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "")
                if not content_type.startswith("image/"):
                    return None
                if len(resp.content) > MAX_IMAGE_BYTES:
                    return None
                return resp.content
        except Exception as e:
            logger.warning("image_url_fetch_failed", url=raw, error=str(e))
            return None

    return None


def _sniff_image_ext(img_bytes: bytes) -> str:
    """Determine a file extension from image content, not from a claimed source string."""
    try:
        from PIL import Image
        import io
        with Image.open(io.BytesIO(img_bytes)) as im:
            fmt = (im.format or "PNG").lower()
            return "jpg" if fmt == "jpeg" else fmt
    except Exception:
        return "png"

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}

for prefix, uri in NS.items():
    ET.register_namespace(prefix, uri)

ET.register_namespace("", "http://schemas.openxmlformats.org/package/2006/relationships")
ET.register_namespace("mc", "http://schemas.openxmlformats.org/markup-compatibility/2006")
ET.register_namespace("c", "http://schemas.openxmlformats.org/drawingml/2006/chart")


class PPTXRenderer:
    """
    Renders a PPTX file by cloning template layout XML and filling placeholders.

    Strategy:
    1. If a template is provided, use it as the base — copy its structure,
       slide layouts, and masters verbatim.
    2. For each slide in the input, clone the appropriate layout XML.
    3. Fill placeholder <a:t> text nodes without modifying any rPr (run properties)
       formatting attributes — this preserves the template's design fidelity.
    4. For images, add relationship entries and <p:pic> elements.
    5. Repack as a valid PPTX ZIP.
    """

    def __init__(
        self,
        template_path: Optional[str] = None,
        color_theme: Optional[str] = None,
        design_system: Optional[dict] = None,
    ) -> None:
        self.template_path = template_path
        # Only meaningful on the no-template path (_render_from_default): an
        # uploaded template's own colors are preserved untouched to keep
        # template fidelity, per this renderer's whole design (see module
        # docstring) - color_theme only picks a palette for the built-in
        # default design system when there is no template to be faithful to.
        self.color_theme = color_theme
        # The extracted (or default) design system for this run - used to
        # style generated content that has no template placeholder of its
        # own to inherit formatting from, namely tables and charts. See
        # table_style.resolve_table_style / chart_renderer for how this is
        # consumed.
        self.design_system = design_system or {}
        # Set by _render_from_default before it adds any slides.
        self._title_rgb = None
        self._bullet_color_hex: Optional[str] = None

    def render(
        self,
        slides: list[dict],
        metadata: dict,
        output_path: str,
    ) -> str:
        if self.template_path and os.path.exists(self.template_path):
            return self._render_from_template(slides, metadata, output_path)
        else:
            return self._render_from_default(slides, metadata, output_path)

    def _render_from_template(
        self,
        slides: list[dict],
        metadata: dict,
        output_path: str,
    ) -> str:
        working_dir = tempfile.mkdtemp(prefix="thinkslides_render_")

        try:
            with zipfile.ZipFile(self.template_path, "r") as zf:
                zf.extractall(working_dir)

            slides_dir = os.path.join(working_dir, "ppt", "slides")
            rels_dir = os.path.join(working_dir, "ppt", "slides", "_rels")

            existing_slides = sorted(
                [f for f in os.listdir(slides_dir) if f.startswith("slide") and f.endswith(".xml")]
            ) if os.path.exists(slides_dir) else []

            for sf in existing_slides:
                os.remove(os.path.join(slides_dir, sf))
                rel_path = os.path.join(rels_dir, sf + ".rels") if os.path.exists(rels_dir) else ""
                if rel_path and os.path.exists(rel_path):
                    os.remove(rel_path)

            layout_map = self._build_layout_map(working_dir)

            for i, slide_data in enumerate(slides):
                layout_id = str(slide_data.get("layoutId", "1"))
                slide_num = i + 1
                slide_file = f"slide{slide_num}.xml"

                layout_file = layout_map.get(layout_id)
                image_rels: list[dict] = []
                if layout_file and os.path.exists(layout_file):
                    slide_xml, image_rels = self._clone_layout_as_slide(
                        layout_file, slide_data, working_dir, slide_num
                    )
                else:
                    slide_xml = self._create_minimal_slide(slide_data)

                os.makedirs(slides_dir, exist_ok=True)
                slide_path = os.path.join(slides_dir, slide_file)
                with open(slide_path, "wb") as f:
                    f.write(slide_xml)

                self._create_slide_rels(working_dir, slide_num, layout_id, image_rels)

            self._update_presentation_xml(working_dir, len(slides))
            self._update_content_types(working_dir, len(slides))
            self._apply_metadata(working_dir, metadata)
            self._repack(working_dir, output_path)

            logger.info("pptx_rendered_from_template", slide_count=len(slides), output=output_path)
            return output_path

        finally:
            shutil.rmtree(working_dir, ignore_errors=True)

    def _render_from_default(
        self,
        slides: list[dict],
        metadata: dict,
        output_path: str,
    ) -> str:
        # Use default template
        template_path = get_default_template_path()
        prs = Presentation(template_path)

        # Resolve the chosen color theme's colors once. The default template
        # file itself doesn't need to change per theme (its master background
        # is white in every theme) - only the accents applied here, at render
        # time, actually vary.
        default_design_system = get_default_design_system(color_theme=self.color_theme)
        palette = default_design_system["palette"]["colors"]

        # Tables and charts are styled from self.design_system (they have no
        # placeholder formatting to inherit). With no template there may be
        # none, in which case they fell back to table_style's own built-in
        # defaults - so a deck rendered in, say, Starbucks green got green
        # titles and bullets but a navy table header. Adopt the resolved
        # theme's design system so every element agrees on the palette.
        if not self.design_system:
            self.design_system = default_design_system

        def _palette_hex(name: str) -> Optional[str]:
            return next((c["value"] for c in palette if c.get("name") == name), None)

        primary_hex = _palette_hex("primary")
        try:
            self._title_rgb = _hex_to_rgbcolor(primary_hex) if primary_hex else None
        except ValueError:
            self._title_rgb = None
        # Brand-tinted bullet markers and title rules, so a deck rendered
        # without a user template still reads as a designed artifact rather
        # than default-black Office output.
        self._bullet_color_hex = _palette_hex("accent") or primary_hex

        for slide_data in slides:
            # Infer layout type from slide content
            layout_type = LayoutEngine.infer_layout_type(slide_data)
            layout_config = LayoutEngine.get_layout(layout_type)

            # Find appropriate layout from template
            layout = self._find_layout_for_type(prs, layout_type)

            slide = prs.slides.add_slide(layout)

            # Render content using intelligent positioning
            self._render_content_on_slide(slide, slide_data, layout_config)

        core_props = prs.core_properties
        if metadata.get("title"):
            core_props.title = metadata["title"]
        if metadata.get("author"):
            core_props.author = metadata["author"]
        if metadata.get("subject"):
            core_props.subject = metadata["subject"]

        prs.save(output_path)
        logger.info("pptx_rendered_default", slide_count=len(slides), output=output_path)
        return output_path

    def _find_layout_for_type(self, prs: Presentation, layout_type: LayoutType):
        """Find the best layout from the default template for a layout type."""
        # Exact names in python-pptx's built-in layout catalog, which is what
        # the default template is built from (see default_template.py).
        #
        # This used to be a substring match over a per-type keyword list, run
        # against every layout in order. Because layout 0 is "Title Slide",
        # the keyword "title" matched it first for LayoutType.TITLE - and
        # "content" matched "Title and Content" only after "blank" failed, so
        # several types collapsed onto the wrong layout. Naming the layouts
        # exactly removes the ambiguity, with the old fuzzy match kept purely
        # as a fallback for templates that don't use the stock catalog.
        preferred_names = {
            LayoutType.TITLE: ["title slide"],
            LayoutType.CONTENT: ["title and content"],
            LayoutType.TWO_COLUMN: ["two content", "comparison"],
            # Object slides take a title-only layout: the picture/table is
            # positioned by the renderer, so an unused body placeholder would
            # only leave an empty "Click to add text" ghost behind it.
            LayoutType.IMAGE: ["title only"],
            LayoutType.TABLE: ["title only"],
            LayoutType.CONCLUSION: ["title and content"],
        }
        names = preferred_names.get(layout_type, ["title and content"])
        by_name = {(lay.name or "").strip().lower(): lay for lay in prs.slide_layouts}

        for name in names:
            if name in by_name:
                return by_name[name]

        for name in names:
            for layout_name, layout in by_name.items():
                if name in layout_name:
                    return layout

        return prs.slide_layouts[0] if prs.slide_layouts else None

    def _render_content_on_slide(
        self,
        slide,
        slide_data: dict,
        layout_config: LayoutType,
    ) -> None:
        """
        Render slide content into the layout's own placeholders.

        ``prs.slides.add_slide(layout)`` copies the layout's placeholders onto
        the new slide. This routine previously ignored them entirely and
        stacked free-floating text boxes at LayoutEngine coordinates instead,
        which produced decks where every slide carried an empty "Title"/
        "Subtitle" placeholder *plus* unaligned text boxes drawn over the top
        of them. Filling the real placeholders keeps the deck aligned to the
        layout's own geometry, and any placeholder left unfilled is deleted so
        no empty prompt box survives into the output.
        """
        contents = slide_data.get("contents", [])
        if not contents:
            self._strip_empty_placeholders(slide)
            return

        text_items = [c for c in contents if c.get("type") == "text"]
        object_items = [c for c in contents if c.get("type") in ("image", "table", "chart")]

        # The content designer emits the title first, then body items (see
        # design_slide_content's prompt), so position in the list is the
        # reliable signal - far more so than the old "bold or >=28pt" sniff,
        # which misfired on any body run the model happened to bold.
        title_item = text_items[0] if text_items else None
        body_items = text_items[1:]

        title_ph, body_phs = self._classify_placeholders(slide)
        used_shapes = set()

        # A centred title belongs on a title/section slide. On a content slide
        # it reads as stock-template filler next to left-aligned bullets, so
        # override the stock layout's centring there.
        center_title = layout_config.layout_type in (LayoutType.TITLE, LayoutType.CONCLUSION)

        if title_item is not None and title_ph is not None:
            self._fill_placeholder(
                title_ph, title_item.get("runs", []), is_title=True, center=center_title
            )
            used_shapes.add(id(title_ph))
        elif title_item is not None:
            # Layout has no title placeholder (shouldn't happen with the stock
            # catalog, but a user template may omit one) - fall back to an
            # explicitly positioned box rather than dropping the title.
            self._add_text_box(
                slide,
                title_item.get("runs", []),
                LayoutEngine.calculate_title_position(layout_config),
                is_title=True,
            )

        has_title = title_item is not None

        # Vertical cursor (inches) for body items that have no placeholder left
        # to go into. Without it, every overflow block would be drawn at the
        # same calculated content position, stacked directly on top of the
        # previous one.
        top_offset = 0.0
        for i, body_item in enumerate(body_items):
            if i < len(body_phs):
                self._fill_placeholder(body_phs[i], body_item.get("runs", []), is_title=False)
                used_shapes.add(id(body_phs[i]))
                continue

            content_pos = LayoutEngine.calculate_content_position(
                layout_config, has_title=has_title
            )
            if top_offset > 0:
                content_pos.top += top_offset
                content_pos.height -= top_offset
            if content_pos.height <= 0:
                # No vertical room left at all - still render (an overflowing
                # box that validate_rendered_output can flag beats silently
                # dropped content) but stop driving the cursor negative.
                content_pos.height = 0.4

            used_height = self._add_text_box(
                slide, body_item.get("runs", []), content_pos, is_title=False
            )
            # Advance by the text's own measured height rather than a flat
            # guess, so a short block leaves no gap and a long one is not
            # overwritten by the next.
            top_offset += min(used_height, content_pos.height) + 0.15

        # Objects (image/table/chart) take the geometry of the next free body
        # placeholder when one exists, so they land in the layout's designed
        # content region instead of a hardcoded rectangle. The placeholder
        # itself is then removed - a graphic frame cannot live inside it.
        free_phs = [ph for ph in body_phs if id(ph) not in used_shapes]
        for obj in object_items:
            ph = free_phs.pop(0) if free_phs else None
            if ph is not None:
                pos = PositionConfig(
                    left=emu_to_inches(ph.left),
                    top=emu_to_inches(ph.top),
                    width=emu_to_inches(ph.width),
                    height=emu_to_inches(ph.height),
                )
                self._remove_shape(ph)
            elif obj.get("type") == "image":
                pos = LayoutEngine.calculate_image_position(layout_config, has_title=has_title)
            else:
                pos = LayoutEngine.calculate_content_position(layout_config, has_title=has_title)

            if obj["type"] == "image":
                self._add_image(slide, obj.get("image", {}), pos)
            elif obj["type"] == "table":
                self._add_table(slide, obj.get("table", {}), pos)
            else:
                self._add_chart(slide, obj.get("chart", {}), pos)

        self._strip_empty_placeholders(slide)

    @staticmethod
    def _remove_shape(shape) -> None:
        """Detach a shape from its slide."""
        element = shape._element
        parent = element.getparent()
        if parent is not None:
            parent.remove(element)

    def _strip_empty_placeholders(self, slide) -> None:
        """
        Delete placeholders that were never filled.

        An empty placeholder is not invisible: PowerPoint shows its "Click to
        add title" prompt in the editor, and LibreOffice draws its outline in
        some export paths. Leaving them behind is what made generated decks
        look like half-finished templates.
        """
        for shape in list(slide.placeholders):
            if shape.placeholder_format.type in _SKIPPED_PLACEHOLDER_TYPES:
                continue
            if shape.has_text_frame and not shape.text_frame.text.strip():
                self._remove_shape(shape)

    @staticmethod
    def _classify_placeholders(slide) -> tuple[Optional[object], list[object]]:
        """
        Split a slide's placeholders into ``(title, [body...])``.

        Body placeholders are returned in reading order (top, then left) so
        that, on a two-content layout, the first body content item lands in
        the left column rather than wherever python-pptx happened to enumerate
        first.
        """
        title_ph = None
        body_phs = []
        for shape in slide.placeholders:
            ph_type = shape.placeholder_format.type
            if ph_type in _TITLE_PLACEHOLDER_TYPES and title_ph is None:
                title_ph = shape
            elif ph_type not in _SKIPPED_PLACEHOLDER_TYPES:
                body_phs.append(shape)
        body_phs.sort(key=lambda s: (s.top or 0, s.left or 0))
        return title_ph, body_phs

    def _fill_placeholder(
        self,
        placeholder,
        runs: list[dict],
        is_title: bool,
        center: bool = False,
    ) -> None:
        """
        Write text runs into an existing placeholder's text frame.

        Sizing/bullet decisions mirror ``_add_text_box`` so both code paths
        produce the same typography; the difference is purely that the box
        already exists and carries the layout's own position and anchoring.
        """
        tf = placeholder.text_frame
        tf.clear()
        tf.word_wrap = True

        resolved_sizes: list[float] = []
        for run_data in runs:
            style = (
                TypographyEngine.get_title_style(1)
                if is_title
                else TypographyEngine.get_body_style(run_data.get("level", 0))
            )
            resolved_sizes.append(
                TypographyEngine.validate_font_size(style.font_size_pt, is_title=is_title)
            )

        blocks = [
            text_fit.TextBlock(text=r.get("text", ""), font_size_pt=size)
            for r, size in zip(runs, resolved_sizes)
        ]
        box_width_pt = emu_to_inches(placeholder.width) * 72.0
        box_height_pt = emu_to_inches(placeholder.height) * 72.0
        font_scale, _ = text_fit.compute_autofit_scale(blocks, box_width_pt, box_height_pt)

        for i, (run_data, base_size) in enumerate(zip(runs, resolved_sizes)):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            if is_title:
                p.alignment = PP_ALIGN.CENTER if center else PP_ALIGN.LEFT
            run = p.add_run()
            run.text = run_data.get("text", "")

            style = (
                TypographyEngine.get_title_style(1)
                if is_title
                else TypographyEngine.get_body_style(run_data.get("level", 0))
            )
            run.font.size = Pt(max(text_fit.MIN_FONT_SCALE * base_size, base_size * font_scale))
            run.font.bold = style.bold or run_data.get("bold", False)
            run.font.italic = style.italic or run_data.get("italic", False)
            run.font.underline = style.underline or run_data.get("underline", False)
            if is_title and self._title_rgb is not None:
                run.font.color.rgb = self._title_rgb

            level = int(run_data.get("level", 0) or 0)
            bulleted = bool(run_data.get("bullet", False)) and not is_title
            if bulleted:
                p.level = level
            _apply_bullet_formatting(p, bulleted, level, self._bullet_color_hex)

    def _add_chart(self, slide, chart_data: dict, pos: PositionConfig) -> None:
        """Render chart_data to a design-system-styled PNG and place it on the slide."""
        chart_bytes = chart_renderer.render_chart_to_png(
            chart_data, self.design_system, pos.width, pos.height
        )
        if not chart_bytes:
            return
        img_stream = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        try:
            img_stream.write(chart_bytes)
            img_stream.close()
            slide.shapes.add_picture(
                img_stream.name,
                Inches(pos.left), Inches(pos.top), Inches(pos.width), Inches(pos.height)
            )
        finally:
            os.unlink(img_stream.name)

    def _add_table(self, slide, table_data: dict, pos: PositionConfig) -> None:
        """Add a native, design-system-styled table to the slide."""
        rows_data = table_data.get("rows", [])
        if not rows_data:
            return
        n_rows = len(rows_data)
        n_cols = max((len(r) for r in rows_data), default=0)
        if n_cols == 0:
            return

        graphic_frame = slide.shapes.add_table(
            n_rows, n_cols, Inches(pos.left), Inches(pos.top), Inches(pos.width), Inches(pos.height)
        )
        table = graphic_frame.table
        style = table_style.resolve_table_style(self.design_system)
        header_row = bool(table_data.get("headerRow"))

        header_rgb = _hex_to_rgbcolor(style.header_bg)
        header_fg_rgb = _hex_to_rgbcolor(style.header_fg)
        band_rgb = _hex_to_rgbcolor(style.band_bg)
        row_rgb = _hex_to_rgbcolor(style.row_bg)

        for r_idx, row in enumerate(rows_data):
            is_header = header_row and r_idx == 0
            for c_idx in range(n_cols):
                cell = table.cell(r_idx, c_idx)
                cell.text = str(row[c_idx]) if c_idx < len(row) else ""
                cell.fill.solid()
                if is_header:
                    cell.fill.fore_color.rgb = header_rgb
                elif r_idx % 2 == 1:
                    cell.fill.fore_color.rgb = band_rgb
                else:
                    cell.fill.fore_color.rgb = row_rgb

                for paragraph in cell.text_frame.paragraphs:
                    for run in paragraph.runs:
                        run.font.size = Pt(14 if is_header else 12)
                        run.font.bold = is_header
                        run.font.name = style.font_name
                        if is_header:
                            run.font.color.rgb = header_fg_rgb

    def _add_text_box(
        self,
        slide,
        runs: list[dict],
        pos: PositionConfig,
        is_title: bool = False,
    ) -> float:
        """Add a text box with proper styling. Returns the estimated height (inches) the text actually occupies."""
        # PositionConfig fields are inches (plain floats, see layout.py) -
        # python-pptx's shape-creation calls take EMU Length values, and
        # silently treat a raw int/float as EMU rather than raising. Passing
        # pos.left/top/etc. directly - as this used to - produced textboxes a
        # few EMU wide (1 inch = 914400 EMU), i.e. invisible: every slide on
        # the no-template render path had its content positioned into a
        # sub-pixel box. This is the "right slide count, no visible content"
        # bug.
        txBox = slide.shapes.add_textbox(
            Inches(pos.left), Inches(pos.top), Inches(pos.width), Inches(pos.height)
        )
        tf = txBox.text_frame
        tf.word_wrap = True
        # Second line of defense: let LibreOffice/PowerPoint's own autofit
        # shrink further at render/open time if our own estimate below still
        # undershoots for a given font's real metrics.
        tf.auto_size = MSO_AUTO_SIZE.NONE

        # Resolve each run's base (pre-fit) font size first, so the fit
        # estimate and the actually-applied size always agree.
        resolved_sizes: list[float] = []
        for run_data in runs:
            if is_title:
                style = TypographyEngine.get_title_style(1)
            else:
                style = TypographyEngine.get_body_style(run_data.get("level", 0))
            size_pt = TypographyEngine.validate_font_size(style.font_size_pt, is_title=is_title)
            resolved_sizes.append(size_pt)

        blocks = [
            text_fit.TextBlock(text=r.get("text", ""), font_size_pt=size)
            for r, size in zip(runs, resolved_sizes)
        ]
        box_width_pt = pos.width * 72.0
        box_height_pt = pos.height * 72.0
        font_scale, _ = text_fit.compute_autofit_scale(blocks, box_width_pt, box_height_pt)

        for i, (run_data, base_size) in enumerate(zip(runs, resolved_sizes)):
            if i == 0:
                p = tf.paragraphs[0]
            else:
                p = tf.add_paragraph()

            run = p.add_run()
            run.text = run_data.get("text", "")

            style = (
                TypographyEngine.get_title_style(1)
                if is_title
                else TypographyEngine.get_body_style(run_data.get("level", 0))
            )
            fitted_size = max(text_fit.MIN_FONT_SCALE * base_size, base_size * font_scale)
            run.font.size = Pt(fitted_size)
            run.font.bold = style.bold or run_data.get("bold", False)
            run.font.italic = style.italic or run_data.get("italic", False)
            run.font.underline = style.underline or run_data.get("underline", False)
            if is_title and self._title_rgb is not None:
                run.font.color.rgb = self._title_rgb

            # Bullets. A title never takes a marker even if the model set the
            # flag, since the title box is a single centered line.
            level = int(run_data.get("level", 0) or 0)
            bulleted = bool(run_data.get("bullet", False)) and not is_title
            if bulleted:
                p.level = level
            _apply_bullet_formatting(p, bulleted, level, self._bullet_color_hex)

        fitted_blocks = [
            text_fit.TextBlock(text=b.text, font_size_pt=b.font_size_pt * font_scale)
            for b in blocks
        ]
        used_height_pt = text_fit.estimate_blocks_height_pt(fitted_blocks, box_width_pt)
        return used_height_pt / 72.0

    def _add_image(self, slide, img_data: dict, pos: PositionConfig) -> None:
        """Add an image to the slide. Supports base64 (raw or data-URI) and remote URL sources."""
        img_bytes = _resolve_image_bytes(img_data)
        if not img_bytes:
            return
        ext = _sniff_image_ext(img_bytes)
        img_stream = tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False)
        try:
            img_stream.write(img_bytes)
            img_stream.close()
            slide.shapes.add_picture(
                img_stream.name,
                Inches(pos.left), Inches(pos.top), Inches(pos.width), Inches(pos.height)
            )
        finally:
            os.unlink(img_stream.name)

    def _build_layout_map(self, working_dir: str) -> dict[str, str]:
        layout_map: dict[str, str] = {}
        layouts_dir = os.path.join(working_dir, "ppt", "slideLayouts")
        if not os.path.exists(layouts_dir):
            return layout_map

        for fname in os.listdir(layouts_dir):
            if fname.startswith("slideLayout") and fname.endswith(".xml"):
                num = fname.replace("slideLayout", "").replace(".xml", "")
                layout_map[num] = os.path.join(layouts_dir, fname)

        return layout_map

    def _clone_layout_as_slide(
        self,
        layout_file: str,
        slide_data: dict,
        working_dir: str,
        slide_num: int,
    ) -> tuple[bytes, list[dict]]:
        tree = ET.parse(layout_file)
        root = tree.getroot()
        image_rels: list[dict] = []

        for content in slide_data.get("contents", []):
            content_type = content.get("type")

            if content_type == "image":
                next_rid = f"rId{2 + len(image_rels)}"  # rId1 is always the layout relationship
                rel = self._add_image_to_cloned_slide(root, content, working_dir, next_rid)
                if rel:
                    image_rels.append(rel)
                continue

            if content_type == "chart":
                next_rid = f"rId{2 + len(image_rels)}"  # charts are rendered images too - share the same rId sequence
                rel = self._add_chart_to_cloned_slide(root, content, working_dir, next_rid)
                if rel:
                    image_rels.append(rel)
                continue

            if content_type == "table":
                self._add_table_to_cloned_slide(root, content)
                continue

            if content_type != "text":
                continue

            placeholder_idx = content.get("placeholderIdx", -1)
            runs = content.get("runs", [])

            for sp in root.findall(".//p:sp", NS):
                ph = sp.find(".//p:ph", NS)
                if ph is not None and int(ph.get("idx", "-1")) == placeholder_idx:
                    txBody = sp.find(".//p:txBody", NS)
                    if txBody is None:
                        continue

                    paragraphs = txBody.findall("a:p", NS)
                    if not paragraphs:
                        continue

                    for p_idx, para in enumerate(paragraphs):
                        existing_runs = para.findall("a:r", NS)

                        if p_idx < len(runs):
                            run_data = runs[p_idx]
                            if existing_runs:
                                a_t = existing_runs[0].find("a:t", NS)
                                if a_t is not None:
                                    a_t.text = run_data.get("text", "")
                            else:
                                a_r = ET.SubElement(para, f"{{{NS['a']}}}r")
                                a_t = ET.SubElement(a_r, f"{{{NS['a']}}}t")
                                a_t.text = run_data.get("text", "")

                            for excess_run in existing_runs[1:]:
                                para.remove(excess_run)
                        elif existing_runs:
                            for excess_run in existing_runs:
                                para.remove(excess_run)

                    if len(runs) > len(paragraphs):
                        # Extra bullets beyond what the template placeholder
                        # itself had paragraphs for. Clone the last template
                        # paragraph's formatting - pPr (bullet marker/indent)
                        # and its first run's rPr (font/size/color) - onto
                        # each new one. Previously these were created bare,
                        # so bullets past the placeholder's own paragraph
                        # count silently lost the template's bullet styling
                        # and typography, making multi-point slides look
                        # thinner/plainer than the template's design.
                        template_para = paragraphs[-1] if paragraphs else None
                        template_pPr = template_para.find("a:pPr", NS) if template_para is not None else None
                        template_run = template_para.find("a:r", NS) if template_para is not None else None
                        template_rPr = template_run.find("a:rPr", NS) if template_run is not None else None

                        for run_data in runs[len(paragraphs):]:
                            new_para = ET.SubElement(txBody, f"{{{NS['a']}}}p")
                            if template_pPr is not None:
                                new_para.append(copy.deepcopy(template_pPr))
                            a_r = ET.SubElement(new_para, f"{{{NS['a']}}}r")
                            if template_rPr is not None:
                                a_r.append(copy.deepcopy(template_rPr))
                            a_t = ET.SubElement(a_r, f"{{{NS['a']}}}t")
                            a_t.text = run_data.get("text", "")

                        paragraphs = txBody.findall("a:p", NS)

                    self._apply_autofit_to_placeholder(sp, txBody, paragraphs)
                    break

        return ET.tostring(root, encoding="unicode", xml_declaration=True).encode("utf-8"), image_rels

    def _apply_autofit_to_placeholder(self, sp: ET.Element, txBody: ET.Element, paragraphs: list[ET.Element]) -> None:
        """
        Estimate whether the just-filled placeholder's text overflows its
        box, and if so set <a:normAutofit fontScale/lnSpcReduction> on its
        <a:bodyPr> - the same mechanism PowerPoint itself writes when text
        overruns a placeholder. This never touches <a:rPr>, preserving
        template fidelity: the run's own declared size is untouched, only a
        render-time scale hint is added.
        """
        box = get_shape_position(sp)
        if box["cx"] <= 0 or box["cy"] <= 0:
            # No explicit xfrm on this placeholder (size inherited from the
            # slide master) - not enough geometry to judge, leave as-is.
            return

        box_width_pt = emu_to_inches(box["cx"]) * 72.0
        box_height_pt = emu_to_inches(box["cy"]) * 72.0

        blocks: list[text_fit.TextBlock] = []
        for para in paragraphs:
            run = para.find("a:r", NS)
            text = "".join(t.text or "" for t in para.findall(".//a:t", NS))
            size_pt = 18.0
            if run is not None:
                rPr = run.find("a:rPr", NS)
                if rPr is not None and rPr.get("sz"):
                    try:
                        size_pt = int(rPr.get("sz")) / 100.0
                    except ValueError:
                        pass
            blocks.append(text_fit.TextBlock(text=text, font_size_pt=size_pt))

        font_scale, ln_reduction = text_fit.compute_autofit_scale(blocks, box_width_pt, box_height_pt)
        if font_scale >= 1.0:
            return

        bodyPr = txBody.find("a:bodyPr", NS)
        if bodyPr is None:
            # <a:bodyPr> must be the first child of <a:txBody> per the OOXML
            # schema (before <a:lstStyle>/<a:p>).
            bodyPr = ET.Element(f"{{{NS['a']}}}bodyPr")
            txBody.insert(0, bodyPr)

        for tag in ("a:noAutofit", "a:spAutoFit", "a:normAutofit"):
            existing = bodyPr.find(tag, NS)
            if existing is not None:
                bodyPr.remove(existing)

        normAutofit = ET.SubElement(bodyPr, f"{{{NS['a']}}}normAutofit")
        normAutofit.set("fontScale", str(int(font_scale * 100000)))
        if ln_reduction:
            normAutofit.set("lnSpcReduction", str(ln_reduction))

    def _add_image_to_cloned_slide(self, root: ET.Element, content: dict, working_dir: str, rel_id: str) -> Optional[dict]:
        """
        Resolve an `image` content item's bytes and position within a cloned
        layout, then delegate to _insert_picture_xml. `rel_id` is the
        already-decided rId for this image (the caller owns rId numbering
        across a slide's relationships). Returns {"rId", "target"} to be
        registered in the slide's .rels file, or None if the image couldn't
        be resolved.
        """
        img_data = content.get("image", {})
        img_bytes = _resolve_image_bytes(img_data)
        if not img_bytes:
            return None

        pos = self._resolve_cloned_slide_position(
            root, content, img_data.get("placeholderIdx"),
            lambda layout_config: LayoutEngine.calculate_image_position(layout_config, has_title=True),
        )
        return self._insert_picture_xml(
            root, working_dir, rel_id, img_bytes, pos,
            alt_text=img_data.get("altText"), shape_label="Picture",
        )

    def _add_chart_to_cloned_slide(self, root: ET.Element, content: dict, working_dir: str, rel_id: str) -> Optional[dict]:
        """
        Render a `chart` content item to a PNG (see chart_renderer, styled
        from this run's design system) and insert it the same way an image
        would be - a chart is just a generated picture from the OOXML
        package's point of view, which is exactly what makes it render
        identically in LibreOffice and PowerPoint.
        """
        chart_data = content.get("chart", {})
        pos = self._resolve_cloned_slide_position(
            root, content, chart_data.get("placeholderIdx"),
            lambda layout_config: LayoutEngine.calculate_content_position(layout_config, has_title=True),
        )
        width_in = emu_to_inches(pos["cx"])
        height_in = emu_to_inches(pos["cy"])
        chart_bytes = chart_renderer.render_chart_to_png(chart_data, self.design_system, width_in, height_in)
        if not chart_bytes:
            return None
        return self._insert_picture_xml(
            root, working_dir, rel_id, chart_bytes, pos,
            alt_text=chart_data.get("title"), shape_label="Chart",
        )

    def _resolve_cloned_slide_position(self, root: ET.Element, content: dict, nested_placeholder_idx, fallback_position_fn) -> dict:
        """
        Shared position resolution for image/chart content on the
        template-clone path: prefer a matching placeholder's own geometry
        (the prompt convention puts placeholderIdx on the content item
        itself, matching "text"; also accept it nested inside image/chart,
        matching the shared pydantic schema), else fall back to the same
        LayoutEngine heuristic the default render path uses.
        """
        placeholder_idx = content.get("placeholderIdx", nested_placeholder_idx)
        if placeholder_idx is not None:
            for sp in root.findall(".//p:sp", NS):
                ph = sp.find(".//p:ph", NS)
                if ph is not None and int(ph.get("idx", "-1")) == placeholder_idx:
                    box = get_shape_position(sp)
                    if box["cx"] > 0 and box["cy"] > 0:
                        return box
                    break

        layout_type = LayoutEngine.infer_layout_type({"contents": [content]})
        layout_config = LayoutEngine.get_layout(layout_type)
        fallback_pos = fallback_position_fn(layout_config)
        return {
            "x": inches_to_emu(fallback_pos.left),
            "y": inches_to_emu(fallback_pos.top),
            "cx": inches_to_emu(fallback_pos.width),
            "cy": inches_to_emu(fallback_pos.height),
        }

    def _insert_picture_xml(
        self,
        root: ET.Element,
        working_dir: str,
        rel_id: str,
        img_bytes: bytes,
        pos: dict,
        alt_text: Optional[str],
        shape_label: str,
    ) -> Optional[dict]:
        """Write image bytes into ppt/media/ and insert a <p:pic> shape referencing them at `pos`."""
        ext = _sniff_image_ext(img_bytes)
        media_dir = os.path.join(working_dir, "ppt", "media")
        os.makedirs(media_dir, exist_ok=True)
        existing = [f for f in os.listdir(media_dir) if re.match(r"^image(\d+)\.", f)]
        next_n = max((int(re.match(r"^image(\d+)\.", f).group(1)) for f in existing), default=0) + 1
        filename = f"image{next_n}.{ext}"
        with open(os.path.join(media_dir, filename), "wb") as f:
            f.write(img_bytes)
        self._ensure_content_type_extension(working_dir, ext)

        existing_ids = [int(cNvPr.get("id")) for cNvPr in root.findall(".//p:cNvPr", NS) if (cNvPr.get("id") or "").isdigit()]
        shape_id = max(existing_ids, default=1) + 1

        spTree = root.find(".//p:spTree", NS)
        pic = ET.SubElement(spTree, f"{{{NS['p']}}}pic")

        nvPicPr = ET.SubElement(pic, f"{{{NS['p']}}}nvPicPr")
        cNvPr = ET.SubElement(nvPicPr, f"{{{NS['p']}}}cNvPr")
        cNvPr.set("id", str(shape_id))
        cNvPr.set("name", alt_text or f"{shape_label} {shape_id}")
        ET.SubElement(nvPicPr, f"{{{NS['p']}}}cNvPicPr")
        ET.SubElement(nvPicPr, f"{{{NS['p']}}}nvPr")

        blipFill = ET.SubElement(pic, f"{{{NS['p']}}}blipFill")
        blip = ET.SubElement(blipFill, f"{{{NS['a']}}}blip")
        blip.set(f"{{{NS['r']}}}embed", rel_id)
        stretch = ET.SubElement(blipFill, f"{{{NS['a']}}}stretch")
        ET.SubElement(stretch, f"{{{NS['a']}}}fillRect")

        spPr = ET.SubElement(pic, f"{{{NS['p']}}}spPr")
        xfrm = ET.SubElement(spPr, f"{{{NS['a']}}}xfrm")
        off = ET.SubElement(xfrm, f"{{{NS['a']}}}off")
        off.set("x", str(pos["x"]))
        off.set("y", str(pos["y"]))
        ext_el = ET.SubElement(xfrm, f"{{{NS['a']}}}ext")
        ext_el.set("cx", str(pos["cx"]))
        ext_el.set("cy", str(pos["cy"]))
        prstGeom = ET.SubElement(spPr, f"{{{NS['a']}}}prstGeom")
        prstGeom.set("prst", "rect")
        ET.SubElement(prstGeom, f"{{{NS['a']}}}avLst")

        return {"rId": rel_id, "target": f"../media/{filename}"}

    def _ensure_content_type_extension(self, working_dir: str, ext: str) -> None:
        ct_path = os.path.join(working_dir, "[Content_Types].xml")
        if not os.path.exists(ct_path):
            return
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "bmp": "image/bmp", "webp": "image/webp"}.get(ext, f"image/{ext}")
        tree = ET.parse(ct_path)
        root = tree.getroot()
        ct_ns = "http://schemas.openxmlformats.org/package/2006/content-types"
        for default in root.findall(f"{{{ct_ns}}}Default"):
            if default.get("Extension", "").lower() == ext.lower():
                return
        default = ET.SubElement(root, f"{{{ct_ns}}}Default")
        default.set("Extension", ext)
        default.set("ContentType", mime)
        tree.write(ct_path, xml_declaration=True, encoding="UTF-8")

    def _add_table_to_cloned_slide(self, root: ET.Element, content: dict) -> None:
        """
        Insert a native OOXML table (<p:graphicFrame><a:tbl>) into a cloned
        layout's spTree for a `table` content item, styled from this run's
        design system (see table_style.resolve_table_style) - e.g. a
        Starbucks-templated deck gets a Starbucks-green header row and
        banded rows without any per-table styling decision from the LLM.
        """
        table_data = content.get("table", {})
        rows_data = table_data.get("rows", [])
        if not rows_data:
            return
        n_cols = max((len(r) for r in rows_data), default=0)
        if n_cols == 0:
            return
        n_rows = len(rows_data)

        # See the equivalent comment in _add_image_to_cloned_slide: accept
        # placeholderIdx on either the content item itself (the prompted
        # convention) or nested inside the table object (the shared
        # pydantic TableContent schema's convention).
        placeholder_idx = content.get("placeholderIdx", table_data.get("placeholderIdx"))
        pos = None
        if placeholder_idx is not None:
            for sp in root.findall(".//p:sp", NS):
                ph = sp.find(".//p:ph", NS)
                if ph is not None and int(ph.get("idx", "-1")) == placeholder_idx:
                    box = get_shape_position(sp)
                    if box["cx"] > 0 and box["cy"] > 0:
                        pos = box
                    break
        if pos is None:
            layout_type = LayoutEngine.infer_layout_type({"contents": [content]})
            layout_config = LayoutEngine.get_layout(layout_type)
            table_pos = LayoutEngine.calculate_table_position(layout_config, has_title=True)
            pos = {
                "x": inches_to_emu(table_pos.left),
                "y": inches_to_emu(table_pos.top),
                "cx": inches_to_emu(table_pos.width),
                "cy": inches_to_emu(table_pos.height),
            }

        style = table_style.resolve_table_style(self.design_system)
        header_row = bool(table_data.get("headerRow"))

        existing_ids = [int(cNvPr.get("id")) for cNvPr in root.findall(".//p:cNvPr", NS) if (cNvPr.get("id") or "").isdigit()]
        shape_id = max(existing_ids, default=1) + 1

        col_width = max(1, pos["cx"] // n_cols)
        row_height = max(1, pos["cy"] // n_rows)

        spTree = root.find(".//p:spTree", NS)
        graphicFrame = ET.SubElement(spTree, f"{{{NS['p']}}}graphicFrame")

        nvGraphicFramePr = ET.SubElement(graphicFrame, f"{{{NS['p']}}}nvGraphicFramePr")
        cNvPr = ET.SubElement(nvGraphicFramePr, f"{{{NS['p']}}}cNvPr")
        cNvPr.set("id", str(shape_id))
        cNvPr.set("name", f"Table {shape_id}")
        cNvGraphicFramePr = ET.SubElement(nvGraphicFramePr, f"{{{NS['p']}}}cNvGraphicFramePr")
        ET.SubElement(cNvGraphicFramePr, f"{{{NS['a']}}}graphicFrameLocks", {"noGrp": "1"})
        ET.SubElement(nvGraphicFramePr, f"{{{NS['p']}}}nvPr")

        xfrm = ET.SubElement(graphicFrame, f"{{{NS['p']}}}xfrm")
        off = ET.SubElement(xfrm, f"{{{NS['a']}}}off")
        off.set("x", str(pos["x"]))
        off.set("y", str(pos["y"]))
        ext_el = ET.SubElement(xfrm, f"{{{NS['a']}}}ext")
        ext_el.set("cx", str(pos["cx"]))
        ext_el.set("cy", str(pos["cy"]))

        graphic = ET.SubElement(graphicFrame, f"{{{NS['a']}}}graphic")
        graphicData = ET.SubElement(graphic, f"{{{NS['a']}}}graphicData")
        graphicData.set("uri", "http://schemas.openxmlformats.org/drawingml/2006/table")

        tbl = ET.SubElement(graphicData, f"{{{NS['a']}}}tbl")
        tblPr = ET.SubElement(tbl, f"{{{NS['a']}}}tblPr")
        tblPr.set("firstRow", "1" if header_row else "0")
        tblPr.set("bandRow", "1")
        tableStyleId = ET.SubElement(tblPr, f"{{{NS['a']}}}tableStyleId")
        # A valid built-in table style GUID - the fill/font colors set
        # per-cell below are what actually carries the design system's
        # branding, this ID just needs to be well-formed.
        tableStyleId.text = "{5940675A-B579-460E-94D1-54222C63F5DA}"

        tblGrid = ET.SubElement(tbl, f"{{{NS['a']}}}tblGrid")
        for _ in range(n_cols):
            gridCol = ET.SubElement(tblGrid, f"{{{NS['a']}}}gridCol")
            gridCol.set("w", str(col_width))

        header_fill = style.header_bg.lstrip("#")
        band_fill = style.band_bg.lstrip("#")
        row_fill = style.row_bg.lstrip("#")
        header_fg = style.header_fg.lstrip("#")

        for r_idx, row in enumerate(rows_data):
            is_header = header_row and r_idx == 0
            tr = ET.SubElement(tbl, f"{{{NS['a']}}}tr")
            tr.set("h", str(row_height))
            fill_hex = header_fill if is_header else (band_fill if r_idx % 2 == 1 else row_fill)
            font_hex = header_fg if is_header else "000000"

            for c_idx in range(n_cols):
                text = str(row[c_idx]) if c_idx < len(row) else ""
                tc = ET.SubElement(tr, f"{{{NS['a']}}}tc")
                txBody = ET.SubElement(tc, f"{{{NS['a']}}}txBody")
                ET.SubElement(txBody, f"{{{NS['a']}}}bodyPr")
                ET.SubElement(txBody, f"{{{NS['a']}}}lstStyle")
                p = ET.SubElement(txBody, f"{{{NS['a']}}}p")
                r = ET.SubElement(p, f"{{{NS['a']}}}r")
                rPr = ET.SubElement(r, f"{{{NS['a']}}}rPr")
                rPr.set("lang", "en-US")
                rPr.set("sz", "1400" if is_header else "1200")
                rPr.set("b", "1" if is_header else "0")
                solidFill = ET.SubElement(rPr, f"{{{NS['a']}}}solidFill")
                srgbClr = ET.SubElement(solidFill, f"{{{NS['a']}}}srgbClr")
                srgbClr.set("val", font_hex)
                t = ET.SubElement(r, f"{{{NS['a']}}}t")
                t.text = text

                tcPr = ET.SubElement(tc, f"{{{NS['a']}}}tcPr")
                tcFill = ET.SubElement(tcPr, f"{{{NS['a']}}}solidFill")
                tcSrgb = ET.SubElement(tcFill, f"{{{NS['a']}}}srgbClr")
                tcSrgb.set("val", fill_hex)

    def _create_minimal_slide(self, slide_data: dict) -> bytes:
        shape_id = 2
        shapes_xml = ""
        for content in slide_data.get("contents", []):
            if content.get("type") == "text":
                text = " ".join(r.get("text", "") for r in content.get("runs", []))
                shapes_xml += f"""
      <p:sp>
        <p:nvSpPr>
          <p:cNvPr id="{shape_id}" name="Content"/>
          <p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>
          <p:nvPr/>
        </p:nvSpPr>
        <p:spPr>
          <a:xfrm>
            <a:off x="457200" y="274638"/>
            <a:ext cx="8229600" cy="5715000"/>
          </a:xfrm>
          <a:prstGeom prst="rect"><a:avLst/></a:prstGeom>
        </p:spPr>
        <p:txBody>
          <a:bodyPr/>
          <a:lstStyle/>
          <a:p><a:r><a:t>{text}</a:t></a:r></a:p>
        </p:txBody>
      </p:sp>"""
                shape_id += 1

        slide_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
       xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
       xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld>
    <p:spTree>
      <p:nvGrpSpPr>
        <p:cNvPr id="1" name=""/>
        <p:cNvGrpSpPr/>
        <p:nvPr/>
      </p:nvGrpSpPr>
      <p:grpSpPr>
        <a:xfrm>
          <a:off x="0" y="0"/>
          <a:ext cx="0" cy="0"/>
          <a:chOff x="0" y="0"/>
          <a:chExt cx="0" cy="0"/>
        </a:xfrm>
      </p:grpSpPr>
{shapes_xml}
    </p:spTree>
  </p:cSld>
</p:sld>"""
        return slide_xml.encode("utf-8")

    def _create_slide_rels(self, working_dir: str, slide_num: int, layout_id: str, image_rels: Optional[list[dict]] = None) -> None:
        rels_dir = os.path.join(working_dir, "ppt", "slides", "_rels")
        os.makedirs(rels_dir, exist_ok=True)
        rels_path = os.path.join(rels_dir, f"slide{slide_num}.xml.rels")

        image_rel_xml = "\n".join(
            f'  <Relationship Id="{rel["rId"]}" '
            f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
            f'Target="{rel["target"]}"/>'
            for rel in (image_rels or [])
        )

        rels_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout"
    Target="../slideLayouts/slideLayout{layout_id}.xml"/>
{image_rel_xml}
</Relationships>"""

        with open(rels_path, "w", encoding="utf-8") as f:
            f.write(rels_xml)

    def _update_presentation_xml(self, working_dir: str, slide_count: int) -> None:
        """Update presentation.xml to reference the correct number of slides."""
        pres_path = os.path.join(working_dir, "ppt", "presentation.xml")
        if not os.path.exists(pres_path):
            return

        tree = ET.parse(pres_path)
        root = tree.getroot()

        # Find the <p:sldIdLst> element. Some templates - including our own
        # generated default, which starts with zero slides - omit it
        # entirely rather than emitting an empty <p:sldIdLst/>. Silently
        # bailing out here previously produced a slide-less package: the
        # rest of the pipeline (slide XML, rels, content types) still ran
        # and reported success, but the file opened with 0 slides - the
        # "downloaded pptx is empty" failure mode. Create it in the correct
        # schema position (after sldMasterIdLst, before sldSz) instead.
        sldIdLst = root.find("p:sldIdLst", NS)
        if sldIdLst is None:
            sldIdLst = ET.Element(f"{{{NS['p']}}}sldIdLst")
            sldMasterIdLst = root.find("p:sldMasterIdLst", NS)
            insert_pos = list(root).index(sldMasterIdLst) + 1 if sldMasterIdLst is not None else 0
            root.insert(insert_pos, sldIdLst)

        # Remove existing slide ID entries
        for sldId in list(sldIdLst):
            sldIdLst.remove(sldId)

        # Assign relationship IDs first (by scanning the actual rels file -
        # see _update_presentation_rels), then reuse the same IDs here so the
        # two files never disagree about which rId maps to which slide.
        rids = self._update_presentation_rels(working_dir, slide_count)

        # Add new slide ID entries. Slide IDs start from 256 by convention.
        for i, rid in enumerate(rids):
            sldId = ET.SubElement(sldIdLst, f"{{{NS['p']}}}sldId")
            sldId.set("id", str(256 + i))
            sldId.set(f"{{{NS['r']}}}id", rid)

        tree.write(pres_path, xml_declaration=True, encoding="UTF-8")

    def _update_presentation_rels(self, working_dir: str, slide_count: int) -> list[str]:
        """Update presentation.xml.rels to reference all slides.

        Returns the relationship IDs assigned, in slide order, so the caller
        can reuse them in <p:sldIdLst> instead of re-deriving them.
        """
        rels_path = os.path.join(working_dir, "ppt", "_rels", "presentation.xml.rels")
        if not os.path.exists(rels_path):
            os.makedirs(os.path.dirname(rels_path), exist_ok=True)
            # Create a minimal rels file
            root = ET.fromstring("""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>""")
        else:
            tree = ET.parse(rels_path)
            root = tree.getroot()

        rel_ns = "http://schemas.openxmlformats.org/package/2006/relationships"

        # Remove existing slide relationships - they get regenerated below.
        for rel in list(root):
            rel_type = rel.get("Type", "")
            if "slide" in rel_type and "layout" not in rel_type and "master" not in rel_type:
                root.remove(rel)

        # Find the next free numeric rId instead of hardcoding rId{i+2}, which
        # assumed rId1 is always the slideMaster and rId2..N+1 were free. Any
        # template whose presentation.xml.rels already used those IDs for
        # something else (a theme, printerSettings, tableStyles, ...) ended up
        # with duplicate Id attributes - invalid OOXML that PowerPoint reports
        # as a file needing repair.
        used_ids = set()
        for rel in root:
            rid = rel.get("Id", "")
            if rid.startswith("rId") and rid[3:].isdigit():
                used_ids.add(int(rid[3:]))
        next_id = max(used_ids, default=1) + 1

        rids = [f"rId{next_id + i}" for i in range(slide_count)]
        for i, rid in enumerate(rids):
            rel = ET.SubElement(root, f"{{{rel_ns}}}Relationship")
            rel.set("Id", rid)
            rel.set("Type", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide")
            rel.set("Target", f"slides/slide{i + 1}.xml")

        tree = ET.ElementTree(root)
        tree.write(rels_path, xml_declaration=True, encoding="UTF-8")
        return rids

    def _update_content_types(self, working_dir: str, slide_count: int) -> None:
        """Update [Content_Types].xml to include all slides."""
        ct_path = os.path.join(working_dir, "[Content_Types].xml")
        if not os.path.exists(ct_path):
            return

        tree = ET.parse(ct_path)
        root = tree.getroot()

        ct_ns = "http://schemas.openxmlformats.org/package/2006/content-types"

        # Remove existing slide overrides
        for override in list(root):
            part_name = override.get("PartName", "")
            if "/slides/slide" in part_name:
                root.remove(override)

        # Add slide content type overrides
        for i in range(slide_count):
            override = ET.SubElement(root, f"{{{ct_ns}}}Override")
            override.set("PartName", f"/ppt/slides/slide{i + 1}.xml")
            override.set(
                "ContentType",
                "application/vnd.openxmlformats-officedocument.presentationml.slide+xml",
            )

        tree.write(ct_path, xml_declaration=True, encoding="UTF-8")

    def _apply_metadata(self, working_dir: str, metadata: dict) -> None:
        """Apply presentation metadata to core.xml."""
        core_path = os.path.join(working_dir, "docProps", "core.xml")
        if not os.path.exists(core_path):
            return

        tree = ET.parse(core_path)
        root = tree.getroot()

        dc_ns = "http://purl.org/dc/elements/1.1/"

        if metadata.get("title"):
            title_elem = root.find(f"{{{dc_ns}}}title")
            if title_elem is None:
                title_elem = ET.SubElement(root, f"{{{dc_ns}}}title")
            title_elem.text = metadata["title"]

        if metadata.get("author"):
            creator_elem = root.find(f"{{{dc_ns}}}creator")
            if creator_elem is None:
                creator_elem = ET.SubElement(root, f"{{{dc_ns}}}creator")
            creator_elem.text = metadata["author"]

        if metadata.get("subject"):
            subject_elem = root.find(f"{{{dc_ns}}}subject")
            if subject_elem is None:
                subject_elem = ET.SubElement(root, f"{{{dc_ns}}}subject")
            subject_elem.text = metadata["subject"]

        tree.write(core_path, xml_declaration=True, encoding="UTF-8")

    def _repack(self, working_dir: str, output_path: str) -> None:
        """Repack the working directory into a PPTX ZIP file."""
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root_dir, dirs, files in os.walk(working_dir):
                for file in files:
                    file_path = os.path.join(root_dir, file)
                    arcname = os.path.relpath(file_path, working_dir)
                    zf.write(file_path, arcname)