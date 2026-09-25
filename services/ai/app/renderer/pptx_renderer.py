"""
PPTX Renderer

Clones real layout XML from the template, fills placeholder elements
without touching rPr formatting attributes, and repacks.
Never recreates design from scratch — always clones and fills.
"""

from __future__ import annotations
import base64
import os
import re
import copy
import zipfile
import shutil
import tempfile
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from typing import Optional
from xml.sax.saxutils import escape
from dataclasses import dataclass, field, replace
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.enum.text import MSO_AUTO_SIZE, MSO_ANCHOR, PP_ALIGN
from pptx.enum.shapes import MSO_SHAPE, PP_PLACEHOLDER
from pptx.oxml.ns import qn
from pptx.oxml.xmlchemy import OxmlElement
from app.observability.logging import get_logger
from app.renderer.layout import LayoutEngine, PositionConfig
from app.renderer.regions import RegionTree
from app.renderer.blocks import BLOCK_TYPES, BlockCompiler
from app.renderer.chrome import ChromeSpec, compile_chrome
from app.renderer import apparatus as apparatus_markers
from app.renderer import emphasis
from app.renderer.apparatus import (
    ApparatusTokens, SlideApparatus, compile_apparatus, content_ceiling,
)
from app.renderer import primitives as prim
from app.renderer.typography import TypeScale
from app.renderer.default_template import get_default_template_path, _hex_to_rgbcolor
from app.renderer import text_fit
from app.renderer import native_table
from app.renderer import table_style
from app.renderer import chart_renderer
from app.renderer import composition
from app.renderer import deck_theme
from app.renderer.deck_theme import DeckTheme, SlideStyle
from app.renderer.xml_utils import (
    describe_picture, get_shape_position, emu_to_inches, inches_to_emu,
)
from app import colors
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

# Vertical breathing room between stacked content bands on one slide.
CONTENT_GAP_IN = 0.18
# Floors for a shared content region, so neither a short text block nor a
# chart gets squeezed into an unreadable sliver.
MIN_TEXT_BAND_IN = 0.4
MIN_OBJECT_BAND_IN = 1.5
# The most of a shared band that text may claim when a chart or table is on
# the slide too. Giving text everything it measured and the object whatever
# was left produced a six-row table compressed into 1.7in and typeset at
# 8pt, under five bullets - on a slide whose entire point was the table.
# When a slide carries a visual, the copy is the supporting act.
MAX_TEXT_SHARE_WITH_OBJECT = 0.42

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

# The stock python-pptx layout each of our catalog layouts is built on. The
# catalog owns the geometry (see design_system.get_default_design_system);
# the stock layout is only there to supply real placeholder shapes, which
# carry the master's text defaults so we are not styling bare text boxes.
_STOCK_LAYOUT_FOR_CATALOG_ID = {
    "title": ["title slide"],
    "section": ["section header"],
    "content": ["title and content"],
    "twoColumn": ["two content", "comparison"],
    "conclusion": ["section header"],
    # Object slides take a title-only layout: the picture/table/chart is
    # positioned by the renderer, so an unused body placeholder would only
    # leave an empty "Click to add text" ghost behind it.
    "image": ["title only"],
    "table": ["title only"],
    "chart": ["title only"],
}


# Shapes the renderer adds as deck chrome rather than content: the accent
# motif and the footer/slide number. Named so that everything downstream -
# geometry validation, dead-space checks, tests - can tell "the deck's own
# furniture" from "the slide's content" instead of measuring a 4pt rule as
# if it were a body element.
# Defined in the primitive layer, which is what names shapes; re-exported
# here because this is where every caller has always imported them from.
CHROME_NAME_PREFIX = prim.CHROME_NAME_PREFIX
is_chrome_shape = prim.is_chrome_shape
CHROME_ACCENT_RULE = f"{CHROME_NAME_PREFIX} Accent Rule"
# A region's own background fill. Chrome rather than content: it is the paper
# a block is printed on, and measuring a half-slide fill as if it were an
# element would tell every dead-space and density check that the slide is
# full when nothing has been written on it yet.
CHROME_REGION_GROUND = f"{CHROME_NAME_PREFIX} Region Ground"

# Content that is placed as an object rather than written into a placeholder.
OBJECT_CONTENT_TYPES = frozenset({"image", "table", "chart", "metrics"})

# A title box taller than this is not a title band - it is a region a
# composition put the title inside, and the band's bottom-anchoring rule does
# not apply to it. The stock title band is 1.1in on a 7.5in canvas.
TITLE_BAND_MAX_IN = 2.4


# How far under a wrapped title the accent rule sits when it has to move. A
# rule is a device for separating the title from the page, so it belongs close
# to what it separates.
RULE_CLEARANCE_IN = 0.12


@dataclass
class SlidePlan:
    """How one slide is laid out and coloured, resolved before anything is drawn."""
    layout_id: str
    style: SlideStyle
    title_region: PositionConfig
    body_regions: list[PositionConfig] = field(default_factory=list)
    accent_rule: Optional[PositionConfig] = None
    # Which part of the deck this slide belongs to. The eyebrow displays it,
    # which is what turns a page number into a position in a document.
    section: str = ""
    # The slide's footnotes and source line. Held on the plan because their
    # height is taken out of the body region before any content is laid out -
    # an apparatus measured afterwards is one with autofitted text on top.
    apparatus: SlideApparatus = field(default_factory=SlideApparatus)
    # The slide's own composition, when it declared one. None for every slide
    # that binds by placeholder index, which is every slide written before
    # regions existed and most written after.
    regions: Optional[RegionTree] = None
    # The canvas, so a mark bound to no region is still positioned
    # fractionally against something real rather than against a guess.
    canvas: PositionConfig = field(
        default_factory=lambda: PositionConfig(
            left=0.0, top=0.0,
            width=LayoutEngine.SLIDE_WIDTH, height=LayoutEngine.SLIDE_HEIGHT,
        )
    )

    @property
    def content_region(self) -> PositionConfig:
        """The union of the body regions - the band shared content divides up."""
        if not self.body_regions:
            return PositionConfig(left=0.62, top=1.66, width=8.76, height=3.34)
        left = min(r.left for r in self.body_regions)
        top = min(r.top for r in self.body_regions)
        right = max(r.left + r.width for r in self.body_regions)
        bottom = max(r.top + r.height for r in self.body_regions)
        return PositionConfig(left=left, top=top, width=right - left, height=bottom - top)


def _shortened(region: PositionConfig, by_in: float) -> PositionConfig:
    """A region with room taken off its bottom edge, never below a usable floor."""
    return PositionConfig(
        left=region.left, top=region.top, width=region.width,
        height=max(MIN_TEXT_BAND_IN, region.height - max(0.0, by_in)),
    )


def _region_from_placeholder_spec(spec: dict) -> PositionConfig:
    """A catalog placeholder's EMU geometry as an inches-based region."""
    return PositionConfig(
        left=emu_to_inches(spec.get("xEmu", 0)),
        top=emu_to_inches(spec.get("yEmu", 0)),
        width=emu_to_inches(spec.get("cxEmu", 0)),
        height=emu_to_inches(spec.get("cyEmu", 0)),
    )


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


# Slide relationship ids for external hyperlinks. Images are numbered up from
# rId2 in the same pass that discovers links, so the two sequences are kept
# apart by starting this one somewhere a slide will never reach.
LINK_RID_BASE = 1000

# The relationship type for a slide part, spelled out once. Matching this by
# substring is how the slide master's own relationship ("slideMaster") used to
# get swept up and deleted - see _update_presentation_rels.
SLIDE_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"
)


def _placeholder_index(ph) -> int:
    """The placeholder index a slide's contents address, as OOXML defines it.

    ``idx`` is optional and defaults to 0, and a title placeholder is exactly
    the one that conventionally omits it - it is identified by ``type="title"``
    instead. Reading it as "no idx means no match" meant no title ever got
    written on the template-clone path: every slide of a deck built on an
    uploaded template kept the layout's own prompt text, "Click to edit Master
    title style".
    """
    raw = ph.get("idx")
    if raw is None:
        return 0 if ph.get("type") in ("title", "ctrTitle") else -1
    try:
        return int(raw)
    except ValueError:
        return -1


def _set_paragraph_level(para, level: int) -> None:
    """Set a cloned paragraph's outline level to the one the deck asked for.

    The layout's own paragraphs run levels 0-4 in order, and filling them in
    order without touching ``lvl`` rendered a flat five-bullet list as a
    five-deep cascade of shrinking indents. The level is the deck's decision
    (design_slide_content sets it per run); the styling at that level stays
    the template's, which is what ``pPr`` otherwise carries.
    """
    pPr = para.find("a:pPr", NS)
    if pPr is None:
        if level <= 0:
            return
        pPr = ET.Element(f"{{{NS['a']}}}pPr")
        para.insert(0, pPr)
    if level <= 0:
        pPr.attrib.pop("lvl", None)
    else:
        pPr.set("lvl", str(level))


def _link_run(a_r, url: str, allocate_rid) -> None:
    """Turn one cloned-template run into a hyperlink.

    The template path writes raw XML rather than going through python-pptx, so
    this writes the ``<a:hlinkClick>`` itself. It is added to the run's existing
    ``<a:rPr>`` - never a replacement - because that element is the template's
    own typography, and this whole renderer exists to leave it alone.
    """
    rPr = a_r.find("a:rPr", NS)
    if rPr is None:
        # rPr must precede a:t, so it goes first rather than being appended.
        rPr = ET.Element(f"{{{NS['a']}}}rPr")
        a_r.insert(0, rPr)
    for existing in rPr.findall("a:hlinkClick", NS):
        rPr.remove(existing)
    hlink = ET.SubElement(rPr, f"{{{NS['a']}}}hlinkClick")
    hlink.set(f"{{{NS['r']}}}id", allocate_rid(url))


def _apply_run_links(para, a_r, run_data: dict, allocate_rid) -> None:
    """Apply whatever links the slide JSON asked for to a cloned-template run.

    Two shapes, matching the default renderer: ``link`` makes the paragraph's
    own run a hyperlink (a reference list line, where the line *is* the
    source), and ``citation`` appends a second run carrying just the "[3]" so
    the marker is the only clickable part of a bullet.
    """
    run_data = run_data or {}

    url = str(run_data.get("link") or "").strip()
    if url:
        _link_run(a_r, url, allocate_rid)

    citation = run_data.get("citation") or {}
    marker_text = str(citation.get("text") or "")
    if not marker_text:
        return

    marker = ET.SubElement(para, f"{{{NS['a']}}}r")
    rPr = a_r.find("a:rPr", NS)
    if rPr is not None:
        # The marker inherits the bullet's own typography, minus any link the
        # bullet itself carried - the marker's link is its own.
        marker_rPr = copy.deepcopy(rPr)
        for existing in marker_rPr.findall("a:hlinkClick", NS):
            marker_rPr.remove(existing)
        marker.append(marker_rPr)
    a_t = ET.SubElement(marker, f"{{{NS['a']}}}t")
    a_t.text = marker_text
    if citation.get("link"):
        _link_run(marker, str(citation["link"]), allocate_rid)


def _clear_text_transform(run) -> None:
    """
    Cancel any inherited capitalisation on a run.

    Several stock Office layouts - "Section Header" most visibly - declare
    ``cap="all"`` on their title placeholder. Text written into one came back
    SHOUTED: a closing slide reading "What we recommend" rendered as
    "WHAT WE RECOMMEND", which is the layout's styling leaking through
    content the deck's own design system never asked to be uppercase.
    python-pptx has no API for the attribute, so it is written directly.
    """
    run._r.get_or_add_rPr().set("cap", "none")


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

CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"


@contextmanager
def _default_namespace(uri: str):
    """Serialise one document with ``uri`` as its default namespace.

    ElementTree keeps a single process-wide prefix map, and "" is claimed
    above by the relationships namespace because every .rels file in the
    package needs it. [Content_Types].xml needs a different one, and an OPC
    part written with a generated ``ns0:`` prefix instead of its own default
    namespace is what LibreOffice refuses to open.
    """
    previous = ET._namespace_map.get(uri)
    ET.register_namespace("", uri)
    try:
        yield
    finally:
        ET._namespace_map.pop(uri, None)
        if previous is not None:
            ET._namespace_map[uri] = previous
        # "" belongs to the relationships namespace for everything else.
        ET.register_namespace("", "http://schemas.openxmlformats.org/package/2006/relationships")


def _pre_rendered_figure(chart_data: dict) -> Optional[bytes]:
    """A figure a provider already drew, if one did.

    build_chart routes to a figure provider at build time rather than here, for
    two reasons that both matter: the fallback is then reported in a tool result
    the model can see, and this render path stays synchronous and offline. The
    cost is that the provider is asked for a full-width exhibit and the PNG is
    placed to fit, which is what happens to a matplotlib render sharing a page
    anyway.
    """
    encoded = str((chart_data or {}).get("renderedPng") or "")
    if not encoded:
        return None
    try:
        return base64.b64decode(encoded)
    except (ValueError, TypeError):
        # Fall through to matplotlib rather than dropping the exhibit.
        return None


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
        # All set by _render_from_default before it adds any slides.
        self._theme: Optional[DeckTheme] = None
        # The deck's resolved type scale - the design system's typefaces and
        # point sizes, with legibility floors applied.
        self._type_scale = TypeScale()
        # The style of the slide currently being written, so the text helpers
        # colour their runs for the background they are actually landing on.
        self._style: Optional[SlideStyle] = None
        # Slide-master placeholder geometry per layout file, parsed on first
        # use. Every slide of a deck typically reuses a handful of layouts.
        self._master_box_cache: dict[str, dict[int, dict]] = {}
        # The 1-based number of the slide being written, so every primitive
        # this renderer emits gets an id that is stable across renders and
        # unique across the deck.
        self._slide_number: int = 0

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
                link_rels: list[dict] = []
                if layout_file and os.path.exists(layout_file):
                    slide_xml, image_rels, link_rels = self._clone_layout_as_slide(
                        layout_file, slide_data, working_dir, slide_num
                    )
                else:
                    slide_xml = self._create_minimal_slide(slide_data)

                os.makedirs(slides_dir, exist_ok=True)
                slide_path = os.path.join(slides_dir, slide_file)
                with open(slide_path, "wb") as f:
                    f.write(slide_xml)

                self._create_slide_rels(
                    working_dir, slide_num, layout_id, image_rels, link_rels
                )

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

        # Tables and charts are styled from self.design_system (they have no
        # placeholder formatting to inherit). With no template there may be
        # none, in which case they fell back to table_style's own built-in
        # defaults - so a deck rendered in, say, Starbucks green got green
        # titles and bullets but a navy table header. Adopt the resolved
        # theme's design system so every element agrees on the palette.
        if not self.design_system:
            self.design_system = default_design_system

        self._theme = DeckTheme.from_design_system(self.design_system)
        self._type_scale = TypeScale.from_design_system(self.design_system)
        # The layout catalog is what the model planned against; it is also
        # what actually positions every shape below, so the two cannot drift.
        catalog = self.design_system if self.design_system.get("layouts") else default_design_system

        total = len(slides)
        for index, slide_data in enumerate(slides):
            plan = self._plan_slide(catalog, slide_data, index, total)
            layout = self._find_layout_for_catalog_id(prs, plan.layout_id)
            slide = prs.slides.add_slide(layout)

            self._slide_number = index + 1
            self._style = plan.style
            self._apply_background(slide, plan.style)
            # Before any content: shapes stack in document order, so a ground
            # added after a block would cover it.
            self._paint_region_grounds(slide, plan)
            self._render_content_on_slide(slide, slide_data, plan)
            self._decorate_slide(slide, plan, catalog, index, total, metadata)
        self._style = None

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

    def _find_layout_for_catalog_id(self, prs: Presentation, layout_id: str):
        """The stock layout that supplies placeholder shapes for a catalog layout.

        Only the shapes matter - every position is overwritten from the
        catalog afterwards. Exact name matching (rather than the old
        substring keyword sweep) because layout 0 is "Title Slide", so the
        keyword "title" matched it first for every type that mentioned title.
        """
        names = _STOCK_LAYOUT_FOR_CATALOG_ID.get(layout_id, ["title and content"])
        by_name = {(lay.name or "").strip().lower(): lay for lay in prs.slide_layouts}

        for name in names:
            if name in by_name:
                return by_name[name]

        for name in names:
            for layout_name, layout in by_name.items():
                if name in layout_name:
                    return layout

        return prs.slide_layouts[0] if prs.slide_layouts else None

    def _plan_slide(
        self,
        catalog: dict,
        slide_data: dict,
        index: int,
        total: int,
    ) -> SlidePlan:
        """
        Resolve which catalog layout a slide uses, and therefore its geometry,
        role and colours - before any shape exists.

        The layout is taken from the slide's own ``layoutId`` when that names
        a real catalog entry (which is what select_layout produced), and only
        inferred from the content otherwise. Inferring unconditionally, as the
        renderer used to, silently discarded the layout the agent had chosen.
        """
        layout_id = str(slide_data.get("layoutId") or "").strip()
        entry = deck_theme.layout_entry(catalog, layout_id)
        if entry is None:
            inferred = LayoutEngine.infer_layout_type(slide_data)
            layout_id = deck_theme.LAYOUT_ID_FOR_TYPE.get(inferred.value, "content")
            entry = deck_theme.layout_entry(catalog, layout_id)

        role = (entry or {}).get("role", deck_theme.ROLE_CONTENT)
        # A deck that opens on a content slide has no cover; force the first
        # slide to read as one so the light/dark rhythm always starts right.
        if index == 0 and role == deck_theme.ROLE_CONTENT and total > 1:
            role = deck_theme.ROLE_COVER
        style = self._theme.style_for(role)

        specs = (entry or {}).get("placeholders") or []
        title_spec = next((s for s in specs if s.get("type") in ("title", "ctrTitle")), None)
        body_specs = [s for s in specs if s.get("type") not in ("title", "ctrTitle")]

        title_region = (
            _region_from_placeholder_spec(title_spec)
            if title_spec
            else PositionConfig(left=0.62, top=0.40, width=8.76, height=0.90)
        )
        body_regions = [_region_from_placeholder_spec(s) for s in body_specs]

        spacing = (catalog.get("spacing") or {})
        rule_spec = spacing.get(
            "heroAccentRule" if role in deck_theme.DARK_ROLES else "accentRule"
        )
        accent_rule = _region_from_placeholder_spec(rule_spec) if rule_spec else None

        dimensions = catalog.get("slideDimensions") or {}
        canvas_width = emu_to_inches(dimensions.get("widthEmu", 0)) or LayoutEngine.SLIDE_WIDTH
        canvas_height = emu_to_inches(dimensions.get("heightEmu", 0)) or LayoutEngine.SLIDE_HEIGHT

        # The apparatus takes its height off the bottom of the body band
        # before anything is written into it. Doing this after the fact is how
        # a footnote block ends up underneath the last bullet.
        apparatus = SlideApparatus.from_slide(slide_data)
        if apparatus:
            reserved = apparatus.height(
                ApparatusTokens.from_design_system(catalog),
                canvas_width * (1.0 - 2 * 0.033),
            )
            body_regions = [_shortened(region, reserved) for region in body_regions]

        return SlidePlan(
            layout_id=layout_id or "content",
            style=style,
            title_region=title_region,
            body_regions=body_regions,
            accent_rule=accent_rule,
            section=str(slide_data.get("section") or "").strip(),
            apparatus=apparatus,
            regions=RegionTree.from_slide(slide_data, canvas_width, canvas_height),
            canvas=PositionConfig(left=0.0, top=0.0,
                                  width=canvas_width, height=canvas_height),
        )

    @staticmethod
    def _apply_background(slide, style: SlideStyle) -> None:
        """Paint the slide's background for its role."""
        try:
            fill = slide.background.fill
            fill.solid()
            fill.fore_color.rgb = _hex_to_rgbcolor(style.background)
        except (ValueError, AttributeError) as e:
            logger.warning("slide_background_skipped", error=str(e))

    def _decorate_slide(
        self,
        slide,
        plan: SlidePlan,
        catalog: dict,
        index: int,
        total: int,
        metadata: dict,
    ) -> None:
        """Draw the deck's repeated chrome: the accent rule and the footer.

        One mark in the same place on every slide is what carries a design
        language across a deck; without it, seven slides of the same white
        page read as seven unrelated documents.
        """
        # The accent rule is positioned by the layout catalog, at a fixed point
        # under where a catalog layout puts its title. A composed slide does
        # not have that title there - the rule landed across the lead
        # paragraph of a split canvas - and its composition is already the
        # design language the rule stands in for on a plain slide. Chrome that
        # knows about compositions is #126; until then a composed slide is
        # left to its own structure rather than having a bar drawn through it.
        if plan.accent_rule is not None and plan.regions is None:
            placed = self._rule_under_title(slide, plan)
            if placed is not None:
                self._add_accent_rule(slide, placed, plan.style.accent_color)

        # Chrome is declared by the design system rather than hardcoded here,
        # so a design direction can move it, drop it, or vary it by slide
        # role - which is one of the things that distinguishes one look from
        # another, and was previously not expressible at all.
        marks = compile_chrome(
            spec=ChromeSpec.from_design_system(catalog),
            style=plan.style,
            type_scale=self._type_scale,
            canvas=plan.canvas,
            role=plan.style.role,
            slide_number=index + 1,
            total_slides=total,
            deck_title=str(metadata.get("title") or ""),
            section_name=str(plan.section or ""),
            has_source_line=bool(plan.apparatus.source_line),
        )
        self._warn_if_composition_reaches_the_apparatus(plan, catalog)
        marks.extend(compile_apparatus(
            apparatus=plan.apparatus,
            tokens=ApparatusTokens.from_design_system(catalog),
            style=plan.style,
            type_scale=self._type_scale,
            canvas=plan.canvas,
        ))
        self._paint_chrome(slide, marks, plan)

    def _paint_chrome(self, slide, marks: list, plan: SlidePlan) -> None:
        """
        Paint each chrome mark against the ground it actually lands on.

        Chrome is compiled once for the slide's own background, which is right
        until a composition puts a dark rail under the corner the footer sits
        in - and then the deck's name is dark ink on a dark panel, legible
        only if you know it is there. #124 made region-bound *content* colour
        itself against its region's ground; this is the same rule applied to
        the furniture that lands on top of one.

        Recolouring the mark is not enough on its own: the painter enforces
        contrast against the context's ground at the last write, so a mark
        told to be near-white while the context still says "white paper" is
        turned straight back into dark ink. The ground has to travel with it.
        """
        tree = plan.regions
        grounded = tree.grounded() if tree is not None else []
        if not marks:
            return
        if not grounded:
            prim.paint_all(slide, marks, self._paint_context())
            return

        by_ground: dict[Optional[str], list] = {}
        for mark in marks:
            by_ground.setdefault(self._ground_under(mark, grounded), []).append(mark)

        for ground, group in by_ground.items():
            style = self._style_on_ground(plan, ground)
            with self._styled(style):
                if style is plan.style:
                    prim.paint_all(slide, group, self._paint_context())
                    continue
                prim.paint_all(slide, [
                    replace(mark, color=(style.footer_color if mark.kind == "text"
                                         else style.accent_color))
                    if mark.color else mark
                    for mark in group
                ], self._paint_context())

    @staticmethod
    def _ground_under(mark, grounded: list) -> Optional[str]:
        """The innermost region ground beneath a mark's centre, if any."""
        box = mark.box
        if box is None:
            return None
        x = box.left + box.width / 2.0
        y = box.top + box.height / 2.0
        found = None
        for region in grounded:
            rect = region.rect
            if (rect.left <= x <= rect.left + rect.width
                    and rect.top <= y <= rect.top + rect.height):
                found = region.ground
        return found

    def _rule_under_title(self, slide, plan: "SlidePlan"):
        """The accent rule, moved under the title that actually rendered.

        The catalog puts it at a fixed height down the page, under where a
        catalog layout *would* put a one-line title. A title that wraps to two
        lines runs straight past it, and the rule is then drawn through
        whatever is beneath - on one real deck, a line through the middle of
        `$1,404 billion`.

        Read off the rendered shapes rather than re-measured: this runs after
        the content is on the slide, so what the title actually did is a fact
        rather than an estimate.

        Returns None when there is nowhere for the rule to go. Chrome that
        cannot be placed is not drawn: a rule is a device for separating the
        title from the page, and one struck through the page is worse than
        none at all.
        """
        rule = plan.accent_rule
        bottom = self._rendered_title_bottom(slide, plan)
        if bottom is None or bottom <= rule.top:
            return rule
        moved = PositionConfig(left=rule.left, top=bottom + RULE_CLEARANCE_IN,
                               width=rule.width, height=rule.height)
        for region in plan.body_regions or ():
            if moved.top + moved.height > region.top:
                return None
        if moved.top + moved.height > LayoutEngine.SLIDE_HEIGHT:
            return None
        return moved

    @staticmethod
    def _rendered_title_bottom(slide, plan: "SlidePlan"):
        """Where the title actually ended, in inches, or None if it is absent."""
        region = plan.title_region
        if region is None:
            return None
        lowest = None
        for shape in slide.shapes:
            if shape.top is None or shape.height is None:
                continue
            top = emu_to_inches(shape.top)
            # A shape that starts in the title's band is the title or part of
            # it; anything starting below it is content.
            if top < region.top - 0.05 or top > region.top + region.height:
                continue
            bottom = top + emu_to_inches(shape.height)
            lowest = bottom if lowest is None else max(lowest, bottom)
        return lowest

    @staticmethod
    def _add_accent_rule(slide, region: PositionConfig, color_hex: str) -> None:
        """A short filled bar - the deck's one repeated motif."""
        try:
            rgb = _hex_to_rgbcolor(color_hex)
        except ValueError:
            return
        shape = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE,
            Inches(region.left), Inches(region.top),
            Inches(region.width), Inches(region.height),
        )
        shape.name = CHROME_ACCENT_RULE
        shape.fill.solid()
        shape.fill.fore_color.rgb = rgb
        shape.line.fill.background()
        # Autoshapes carry a <p:style> whose effectRef points at the theme's
        # shadow. An empty <a:effectLst/> (what shadow.inherit = False writes)
        # is the documented override, but LibreOffice still honoured the
        # effectRef and drew a soft grey shadow under the bar, which reads as
        # a rendering artifact rather than a design element. Dropping the
        # whole style reference leaves nothing to inherit from - the shape's
        # own explicit fill and line are all that is left.
        style = shape._element.find(qn("p:style"))
        if style is not None:
            shape._element.remove(style)

    def _warn_if_composition_reaches_the_apparatus(self, plan: SlidePlan,
                                                   catalog: dict) -> None:
        """
        A composed slide can over-commit its page.

        Placeholder-bound content has the apparatus's height taken out of its
        band before anything is written. A region tree is authored, and
        shrinking it after the fact would distort a composition somebody wrote
        deliberately - so the collision is reported rather than silently
        resolved, and the rendered-output check remains the ground truth.
        """
        if plan.regions is None or not plan.apparatus:
            return
        ceiling = content_ceiling(
            plan.apparatus, ApparatusTokens.from_design_system(catalog), plan.canvas
        )
        for region_id in plan.regions.ids():
            rect = plan.regions.rect(region_id)
            if rect is not None and rect.top + rect.height > ceiling + 0.02:
                logger.warning(
                    "region_reaches_the_apparatus_band",
                    slide=self._slide_number, region=region_id,
                    region_bottom_in=round(rect.top + rect.height, 2),
                    ceiling_in=round(ceiling, 2),
                )

    def _render_content_on_slide(
        self,
        slide,
        slide_data: dict,
        plan: SlidePlan,
    ) -> None:
        """
        Render slide content into the plan's regions, reusing the stock
        layout's placeholder shapes where they exist.

        ``prs.slides.add_slide(layout)`` copies the layout's placeholders onto
        the new slide. This routine previously ignored them entirely and
        stacked free-floating text boxes at LayoutEngine coordinates instead,
        which produced decks where every slide carried an empty "Title"/
        "Subtitle" placeholder *plus* unaligned text boxes drawn over the top
        of them. It then swung the other way and deferred to the stock
        placeholders' own geometry - which is python-pptx's 4:3 Office
        template rescaled, not the catalog the agent designed against.

        Both positions are now resolved the same way: the placeholder shapes
        are reused (they carry the master's text defaults) but every one of
        them is moved onto the catalog region for its slot, so what the model
        planned and what renders are the same rectangle.
        """
        contents = slide_data.get("contents", [])
        # Authored marks are drawn first, so a tinted panel or a rule the slide
        # asked for sits behind the content it frames rather than over it. A
        # slide that wants a mark on top says so by ordering, which is the one
        # thing z-order in a flat list can express.
        self._draw_authored_primitives(slide, slide_data, plan)
        if not contents:
            self._strip_empty_placeholders(slide)
            return

        # A slide may compose itself into named regions instead of, or as well
        # as, binding to placeholder slots. Blocks that name a region it
        # actually declares are drawn there; everything else goes down the
        # path it always went down, untouched. A slide with no region tree
        # never reaches the branch at all.
        bound, contents = self._partition_by_region(contents, plan.regions)
        if bound:
            self._render_regions(slide, bound, plan, self._slide_title(contents, bound))
        if not contents:
            self._strip_empty_placeholders(slide)
            return

        text_items = [c for c in contents if c.get("type") == "text"]
        object_items = [
            c for c in contents
            if c.get("type") in OBJECT_CONTENT_TYPES or c.get("type") in BLOCK_TYPES
        ]

        # The content designer emits the title first, then body items (see
        # design_slide_content's prompt), so position in the list is the
        # reliable signal - far more so than the old "bold or >=28pt" sniff,
        # which misfired on any body run the model happened to bold.
        title_item = text_items[0] if text_items else None
        body_items = text_items[1:]
        slide_title = " ".join(
            str(run.get("text", "")) for run in (title_item or {}).get("runs", [])
        ).strip()

        title_ph, body_phs = self._classify_placeholders(slide)

        if title_item is not None:
            if title_ph is not None:
                self._resize_shape(title_ph, plan.title_region)
                self._fill_placeholder(
                    title_ph, title_item.get("runs", []), is_title=True,
                )
            else:
                # Layout has no title placeholder (shouldn't happen with the
                # stock catalog, but a user template may omit one) - fall back
                # to an explicitly positioned box rather than dropping it.
                self._add_text_box(
                    slide, title_item.get("runs", []), plan.title_region, is_title=True,
                )

        # Body text and objects share the slide's content area, so they have to
        # be allocated space TOGETHER.
        #
        # Filling every body placeholder with text first and only then falling
        # objects back to LayoutEngine's content rectangle meant that a slide
        # carrying both bullets and a chart - the common shape for a
        # contentType:"chart" slide, which still lists its key points - drew
        # the chart directly on top of the bullets. Validation flagged the
        # collision correctly, but no amount of rewording by the content model
        # could fix it: the renderer recreated it on every retry. That is what
        # turned a single bad slide into an endless redesign loop.
        body_entries = [("text", item) for item in body_items]
        body_entries += [(item.get("type", "image"), item) for item in object_items]

        if not body_entries:
            self._strip_empty_placeholders(slide)
            return

        if len(body_entries) == len(plan.body_regions):
            # The catalog declared a slot per item - a two-column layout's two
            # columns, a picture layout's image and caption. Use them as
            # authored rather than re-deriving a stack.
            regions = plan.body_regions
        else:
            # Fewer items than slots is as wrong as more. Filling only the
            # first slot left a two-column layout with one narrow column of
            # text wrapping at half width and the other half of the slide
            # blank - which is what the model gets when it picks twoColumn
            # and then writes a single body block.
            regions = self._split_region_vertically(plan.content_region, body_entries)

        for i, ((kind, item), region) in enumerate(zip(body_entries, regions)):
            ph = body_phs[i] if i < len(body_phs) else None

            if kind == "text":
                if ph is not None:
                    # Keep the placeholder (it carries the layout's typography)
                    # but move it onto its allocated band.
                    self._resize_shape(ph, region)
                    self._fill_placeholder(ph, item.get("runs", []), is_title=False)
                else:
                    self._add_text_box(slide, item.get("runs", []), region, is_title=False)
                continue

            # Objects cannot live inside a placeholder - a graphic frame or
            # picture replaces it, so the placeholder is removed.
            if ph is not None:
                self._remove_shape(ph)

            self._draw_block(slide, kind, item, region, slide_title)

        self._strip_empty_placeholders(slide)

    @staticmethod
    def _partition_by_region(
        contents: list[dict],
        tree: Optional[RegionTree],
    ) -> tuple[list[tuple[str, dict]], list[dict]]:
        """
        Split a slide's blocks into ``(region-bound, everything else)``.

        A block naming a region the slide does not declare is *not* treated as
        region-bound - it falls through to the placeholder path rather than
        being dropped. Validation reports the dangling id; the renderer's job
        at that point is to still put the content on the slide.
        """
        if not tree:
            return [], list(contents)

        bound: list[tuple[str, dict]] = []
        rest: list[dict] = []
        for item in contents:
            if not isinstance(item, dict):
                rest.append(item)
                continue
            resolved = tree.get(item.get("regionId"))
            if resolved is None:
                rest.append(item)
            else:
                bound.append((resolved.id, item))
        return bound, rest

    @staticmethod
    def _slide_title(unbound: list[dict], bound: list[tuple[str, dict]]) -> str:
        """The slide's headline, wherever it was bound - charts read it for context."""
        for item in unbound:
            if isinstance(item, dict) and item.get("type") == "text":
                return " ".join(
                    str(run.get("text", "")) for run in item.get("runs", [])
                ).strip()
        for _, item in bound:
            if item.get("type") == "text":
                return " ".join(
                    str(run.get("text", "")) for run in item.get("runs", [])
                ).strip()
        return ""

    def _paint_region_grounds(self, slide, plan: SlidePlan) -> None:
        """
        Fill each region that declared a ground of its own.

        A slide used to have exactly one background, and text was coloured
        from it. The moment a slide has two grounds that arrangement fails in
        the worst possible way - half the text is correct and half is
        invisible - which is the same defect ``_apply_text_color`` was written
        to fix, returning at a larger scale.

        Painted outermost first, so a rail inside a panel sits on top of it
        rather than under it.
        """
        if plan.regions is None:
            return
        painted = [
            self._add_ground(slide, resolved.rect, resolved.region.ground or "")
            for resolved in plan.regions.grounded()
        ]
        # add_slide clones the layout's placeholders onto the slide before any
        # of this runs, so a ground painted "first" is still above them.
        prim.send_to_back([shape for shape in painted if shape is not None])

    @staticmethod
    def _add_ground(slide, region: PositionConfig, color_hex: str):
        """A filled rectangle standing in for a region's own paper."""
        if region.width <= 0 or region.height <= 0:
            return None
        try:
            rgb = _hex_to_rgbcolor(color_hex)
        except ValueError:
            return None
        shape = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE,
            Inches(region.left), Inches(region.top),
            Inches(region.width), Inches(region.height),
        )
        shape.name = CHROME_REGION_GROUND
        shape.fill.solid()
        shape.fill.fore_color.rgb = rgb
        shape.line.fill.background()
        # Same reason as the accent rule: an autoshape's <p:style> points at
        # the theme's shadow, and LibreOffice draws it even when the effect
        # list is cleared. A ground with a drop shadow reads as a floating
        # card, not as the page.
        style = shape._element.find(qn("p:style"))
        if style is not None:
            shape._element.remove(style)
        return shape

    def _style_on_ground(self, plan: SlidePlan, ground: Optional[str]) -> SlideStyle:
        """The slide's style, re-resolved for a region's own ground."""
        if not ground or self._theme is None:
            return plan.style
        if ground.upper() == (plan.style.background or "").upper():
            return plan.style
        return self._theme.style_on(ground, plan.style.role)

    @contextmanager
    def _styled(self, style: SlideStyle):
        """Colour everything drawn inside the block for ``style``'s ground."""
        previous = self._style
        self._style = style
        try:
            yield
        finally:
            self._style = previous

    def _render_regions(
        self,
        slide,
        bound: list[tuple[str, dict]],
        plan: SlidePlan,
        slide_title: str,
    ) -> None:
        """
        Draw the blocks that named a region, inside that region.

        Region-bound blocks are drawn as explicitly positioned shapes rather
        than into the stock layout's placeholders. A composed slide's regions
        are its own geometry; borrowing a placeholder would mean inheriting a
        box the composition did not ask for, and there is no reason to expect
        the layout to have one per region anyway.

        Two or more blocks in the same region stack inside it, through the
        same allocation that divides a body band - so a region behaves like a
        smaller slide, and autofit and composition measure against the region
        the block is actually in rather than the whole content area.
        """
        tree = plan.regions
        if tree is None:
            return

        grouped: dict[str, list[dict]] = {}
        for region_id, item in bound:
            grouped.setdefault(region_id, []).append(item)

        first_text_id = id(next(
            (item for _, item in bound if item.get("type") == "text"), None
        ))

        for region_id, items in grouped.items():
            rect = tree.rect(region_id)
            if rect is None or rect.width <= 0 or rect.height <= 0:
                continue
            entries = [(item.get("type", "text"), item) for item in items]
            rects = (
                [rect] if len(entries) == 1
                else self._split_region_vertically(rect, entries)
            )
            # Text, bullets, rules and chart axes inside this region are
            # coloured for the ground the region actually sits on, which is
            # not necessarily the slide's.
            ground = tree.ground(region_id)
            with self._styled(self._style_on_ground(plan, ground)):
                for (kind, item), target in zip(entries, rects):
                    is_title = kind == "text" and id(item) == first_text_id
                    self._draw_block(slide, kind, item, target, slide_title,
                                     is_title, ground)

    def _draw_block(
        self,
        slide,
        kind: str,
        item: dict,
        rect: PositionConfig,
        slide_title: str,
        is_title: bool = False,
        ground: Optional[str] = None,
    ) -> None:
        """Draw one content block into an explicit rectangle."""
        if kind == "text":
            self._add_text_box(slide, item.get("runs", []), rect, is_title=is_title)
        elif kind == "image":
            self._add_image(slide, item.get("image", {}), rect)
        elif kind == "table":
            self._add_table(slide, item.get("table", {}), rect)
        elif kind == "metrics":
            self._add_key_metrics(slide, item.get("metrics", {}), rect)
        elif kind == "chart":
            self._add_chart(slide, item.get("chart", {}), rect, slide_title, ground)
        elif kind in BLOCK_TYPES:
            self._draw_typed_block(slide, kind, item, rect)

    def _draw_authored_primitives(self, slide, slide_data: dict,
                                  plan: SlidePlan) -> None:
        """
        Draw the marks a slide placed itself.

        This is the escape hatch, and it is deliberately the same layer the
        blocks compile to: a slide reaching for it to draw a rule no block
        covers is still inside the region model, still contrast-enforced and
        still measured. Nothing is given up by using it except the semantics a
        typed block would have carried.
        """
        specs = slide_data.get("primitives")
        if not isinstance(specs, list) or not specs:
            return

        canvas = plan.canvas
        tree = plan.regions
        compiled = prim.resolve(
            specs,
            frame_for=(lambda region_id: tree.rect(region_id)) if tree else None,
            canvas=canvas,
            id_prefix=f"s{self._slide_number}-mark",
        )
        painted = []
        for primitive in compiled:
            # A mark bound to a region is coloured for that region's ground,
            # exactly as a block bound there would be.
            ground = tree.ground(primitive.region_id) if tree else None
            with self._styled(self._style_on_ground(plan, ground)):
                painted.append(prim.paint(slide, primitive, self._paint_context()))
        prim.send_to_back([shape for shape in painted if shape is not None])

    def _draw_typed_block(self, slide, kind: str, item: dict,
                          rect: PositionConfig) -> None:
        """Compile a block to marks and paint them."""
        compiled = self._compiler().fit(
            kind, item, rect, id_prefix=f"s{self._slide_number}-{kind}"
        )
        extent = prim.bounds(compiled)
        if extent is not None and extent.height > rect.height + 0.02:
            # The block shrank to its legibility floor and still does not fit.
            # That is a planning problem, not a rendering one, and it should
            # be visible without diffing a PDF.
            logger.warning(
                "block_does_not_fit_its_region",
                slide=self._slide_number, block=kind,
                needed_in=round(extent.height, 2),
                available_in=round(rect.height, 2),
            )
        prim.paint_all(slide, compiled, self._paint_context())

    def _compiler(self) -> BlockCompiler:
        """A block compiler for the ground currently being written on."""
        return BlockCompiler(
            style=self._current_style,
            type_scale=self._type_scale,
            design_system=self.design_system,
        )

    def _paint_context(self) -> prim.PaintContext:
        style = self._current_style
        return prim.PaintContext(
            ground=style.background,
            default_color=style.body_color,
            type_scale=self._type_scale,
            default_size_pt=self._type_scale.body_style(0).font_size_pt,
            default_font=self._type_scale.body_font,
        )

    @property
    def _current_style(self) -> SlideStyle:
        """The style of the ground being written on, whatever set it."""
        if self._style is not None:
            return self._style
        theme = self._theme or deck_theme.DeckTheme.from_design_system(
            self.design_system)
        return theme.style_for(deck_theme.ROLE_CONTENT)

    def _measure_block(self, kind: str, item: dict, width_in: float) -> float:
        """How much height a typed block needs, before anything is drawn."""
        return self._compiler().measure(kind, item, width_in)

    @staticmethod
    def _placeholder_region(shape) -> PositionConfig:
        """The shape's geometry as an inches-based PositionConfig."""
        return PositionConfig(
            left=emu_to_inches(shape.left),
            top=emu_to_inches(shape.top),
            width=emu_to_inches(shape.width),
            height=emu_to_inches(shape.height),
        )

    @staticmethod
    def _resize_shape(shape, region: PositionConfig) -> None:
        """Move/resize an existing shape onto ``region``."""
        shape.left = Inches(region.left)
        shape.top = Inches(region.top)
        shape.width = Inches(region.width)
        shape.height = Inches(region.height)

    def _split_region_vertically(
        self,
        base: PositionConfig,
        entries: list[tuple[str, dict]],
    ) -> list[PositionConfig]:
        """
        Divide ``base`` into one stacked band per entry.

        Text asks for the height it actually measures (so a two-line intro
        above a chart does not claim half the slide), objects share whatever
        is left but never drop below a legible minimum, and the whole stack is
        scaled down proportionally if the requests exceed the region. Every
        returned band is inside ``base``, which is what guarantees the chart
        can no longer land on the bullets or run off the slide.
        """
        count = len(entries)
        gap = CONTENT_GAP_IN
        available = base.height - gap * (count - 1)
        if available <= 0:
            gap = 0.0
            available = base.height

        desired: list[Optional[float]] = []
        for kind, item in entries:
            if kind in BLOCK_TYPES:
                # A typed block knows its own height: a stat callout is a
                # figure over a caption, a set of row bands is the sum of its
                # rows. Treating one as an "object" and handing it an equal
                # share is how a two-line banner ended up claiming as much of
                # the slide as a chart.
                desired.append(self._measure_block(kind, item, base.width))
                continue
            if kind != "text":
                desired.append(None)
                continue
            blocks = []
            for run in item.get("runs", []):
                style = self._type_scale.body_style(run.get("level", 0))
                blocks.append(text_fit.TextBlock(
                    text=run.get("text", ""),
                    font_size_pt=style.font_size_pt,
                ))
            measured = text_fit.estimate_blocks_height_pt(blocks, base.width * 72.0) / 72.0
            desired.append(max(measured, MIN_TEXT_BAND_IN))

        object_count = sum(1 for d in desired if d is None)
        if object_count:
            text_total = sum(d for d in desired if d is not None)
            text_cap = available * MAX_TEXT_SHARE_WITH_OBJECT
            if text_total > text_cap and text_total > 0:
                # Shrink the text bands proportionally rather than the object.
                # The text still fits: _write_runs autofits into whatever band
                # it is finally given, down to its own legibility floor.
                squeeze = text_cap / text_total
                desired = [None if d is None else max(MIN_TEXT_BAND_IN, d * squeeze)
                           for d in desired]
                text_total = sum(d for d in desired if d is not None)

            share = (available - text_total) / object_count
            share = max(share, MIN_OBJECT_BAND_IN)
            desired = [share if d is None else d for d in desired]

        total = sum(desired)
        if total > available and total > 0:
            scale = available / total
            desired = [d * scale for d in desired]

        regions: list[PositionConfig] = []
        top = base.top
        for height in desired:
            regions.append(PositionConfig(
                left=base.left, top=top, width=base.width, height=height
            ))
            top += height + gap
        return regions

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
    ) -> None:
        """
        Write text runs into an existing placeholder's text frame.

        Typography and bullets are shared with ``_add_text_box`` via
        ``_write_runs``; the difference is only that the box already exists.
        A balanced body block is moved down inside its own band rather than
        being flushed to the top of it - see ``_write_runs``.
        """
        box_width_in = emu_to_inches(placeholder.width)
        box_height_in = emu_to_inches(placeholder.height)

        balance = self._write_runs(
            placeholder.text_frame, runs, is_title, box_width_in, box_height_in
        )

        if balance.top_offset_in > 0:
            # Move the top edge down and pull the height in by the same
            # amount, so the block sits lower without its bottom edge
            # reaching any further into the footer band.
            placeholder.top = Inches(emu_to_inches(placeholder.top) + balance.top_offset_in)
            placeholder.height = Inches(max(0.1, box_height_in - balance.top_offset_in))

    def _write_runs(
        self,
        tf,
        runs: list[dict],
        is_title: bool,
        box_width_in: float,
        box_height_in: float,
    ) -> composition.BodyBalance:
        """
        Write ``runs`` into a text frame in the deck's own typography.

        Returns the vertical balance that was applied, so the caller can
        place the block: a body block short enough to leave the bottom half
        of the slide empty is set a little larger, led a little further
        apart, and started lower down its band. That combination is what
        stops a three-bullet slide reading as a half-finished page.
        """
        tf.clear()
        tf.word_wrap = True
        # A title sits at the bottom of its band, directly above the accent
        # rule, so a one-line and a two-line title both start from the same
        # baseline and the motif never floats away from the text. Stock Office
        # layouts centre their titles vertically, which made the gap between
        # title and rule depend on how long the title happened to be.
        #
        # That reasoning is about a *band*. A title bound to a full-height
        # region - the rail on reference page 3 - has no rule under it to hang
        # from, and bottom-anchoring one drops it past the foot of the page
        # and into the footer.
        title_in_a_band = is_title and box_height_in <= TITLE_BAND_MAX_IN
        tf.vertical_anchor = MSO_ANCHOR.BOTTOM if title_in_a_band else MSO_ANCHOR.TOP

        styles = [self._style_for_run(run_data, is_title) for run_data in runs]
        # A citation marker is a second, hyperlinked run on the same paragraph
        # (see below), so its width counts towards the fit.
        texts = [
            str(r.get("text", "")) + str((r.get("citation") or {}).get("text", ""))
            for r in runs
        ]

        balance = composition.NO_CHANGE
        if not is_title and runs:
            balance = composition.balance_body(
                texts,
                [style.font_size_pt for style in styles],
                box_width_in,
                box_height_in,
            )

        resolved_sizes = [style.font_size_pt * balance.size_scale for style in styles]

        blocks = [
            text_fit.TextBlock(text=text, font_size_pt=size)
            for text, size in zip(texts, resolved_sizes)
        ]
        box_width_pt = box_width_in * 72.0
        box_height_pt = box_height_in * 72.0
        # The gaps between paragraphs are part of the block's height, so the
        # autofit has to fit the text into what is left after them.
        gap_total_pt = balance.paragraph_gap_pt * max(0, len(runs) - 1)
        font_scale, _ = text_fit.compute_autofit_scale(
            blocks, box_width_pt, max(1.0, box_height_pt - gap_total_pt)
        )

        for i, (run_data, style, base_size) in enumerate(zip(runs, styles, resolved_sizes)):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            # Explicit on every paragraph, not just titles: stock Office
            # layouts centre their subtitle placeholder, so a cover slide's
            # subtitle floated centre-of-slide while its title sat hard left.
            p.alignment = PP_ALIGN.LEFT
            if i > 0 and balance.paragraph_gap_pt > 0:
                p.space_before = Pt(balance.paragraph_gap_pt)

            run = p.add_run()
            # Markdown emphasis is resolved rather than rendered. A model
            # asked for prose writes markdown sooner or later, and a real deck
            # shipped `*Cars 2* leads in worldwide earnings` - asterisks and
            # all - on a slide a customer opened.
            written = emphasis.read(
                apparatus_markers.strip_markers(run_data.get("text", "")))
            run.text = written.text

            # A run may carry a URL (citation markers and the reference list -
            # see renderer/citations.py). Set before the colour below, because
            # PowerPoint only substitutes its own hyperlink colour when the run
            # has no explicit fill, and the deck's palette should win.
            link = str(run_data.get("link") or "").strip()
            if link:
                run.hyperlink.address = link

            font_size = Pt(
                max(text_fit.MIN_FONT_SCALE * base_size, base_size * font_scale)
            )
            run.font.name = style.font_name
            run.font.size = font_size
            run.font.bold = (style.bold or run_data.get("bold", False)
                             or written.bold)
            run.font.italic = (style.italic or run_data.get("italic", False)
                               or written.italic)
            run.font.underline = style.underline or run_data.get("underline", False)
            self._apply_text_color(run, is_title)
            _clear_text_transform(run)

            # A citation marker rides on the end of its bullet as a second run,
            # so only the "[3]" is the link - not the whole sentence, which is
            # what a paragraph-level link would have made clickable.
            citation = run_data.get("citation") or {}
            marker_text = str(citation.get("text") or "")
            if marker_text:
                marker = p.add_run()
                marker.text = marker_text
                if citation.get("link"):
                    marker.hyperlink.address = str(citation["link"])
                marker.font.name = style.font_name
                marker.font.size = font_size
                # A reference marker is apparatus, not a link a reader clicks.
                # Left to a renderer's own hyperlink styling it comes out blue
                # and underlined, which on a dark ground is unreadable and
                # reads as a broken link rather than as a footnote.
                marker.font.underline = False
                self._apply_text_color(marker, is_title)
                _clear_text_transform(marker)

            # A footnote marker is written inline (see renderer/apparatus)
            # and becomes a real superscript run rather than a Unicode glyph,
            # so it scales with the copy around it.
            if apparatus_markers.has_marker(run_data.get("text", "")):
                self._rewrite_with_markers(
                    p, run, run_data.get("text", ""), style.font_name,
                    font_size, is_title,
                )

            level = int(run_data.get("level", 0) or 0)
            bulleted = (
                bool(run_data.get("bullet", False))
                and not is_title
                and not self._is_cover
            )
            if bulleted:
                p.level = level
            _apply_bullet_formatting(p, bulleted, level, self._bullet_hex())

        return balance

    def _rewrite_with_markers(self, paragraph, run, text: str, font_name: str,
                              font_size, is_title: bool) -> None:
        """
        Re-emit a run that carries footnote markers, as alternating body and
        superscript runs.

        The run is already on the paragraph carrying the marker-stripped text,
        so the first part reuses it and the rest are appended - which keeps the
        typography that was already applied rather than rebuilding it.
        """
        parts = apparatus_markers.split_markers(text)
        if not parts:
            return
        first_text, first_marker = parts[0]
        run.text = first_text
        if first_marker is not None:
            apparatus_markers.apply_superscript(run)

        for part_text, marker in parts[1:]:
            extra = paragraph.add_run()
            extra.text = part_text
            extra.font.name = font_name
            extra.font.size = font_size
            self._apply_text_color(extra, is_title)
            _clear_text_transform(extra)
            if marker is not None:
                apparatus_markers.apply_superscript(extra)

    @property
    def _is_cover(self) -> bool:
        """A cover slide's body is a standfirst, not a list.

        The planner is told the title slide takes 0-1 key points and the
        model routinely gives it three anyway. Bulleting them turns the
        opening slide into an agenda nobody asked for; setting them as
        running lines keeps it a cover whatever it was handed.
        """
        return self._style is not None and self._style.role == deck_theme.ROLE_COVER

    def _style_for_run(self, run_data: dict, is_title: bool):
        """The deck's own typography for one run.

        Hero titles (cover, section, closing) step up a size: they are the
        only thing on the slide, so the content-slide title size reads small
        there. The scale itself comes from the run's design system, which is
        the point - an uploaded template's fonts were previously extracted,
        shown to the model, and then rendered in the base template's theme
        font anyway because ``run.font.name`` was never assigned.
        """
        if is_title:
            hero = self._style is not None and self._style.role in deck_theme.DARK_ROLES
            return self._type_scale.title_style(hero=hero)
        return self._type_scale.body_style(run_data.get("level", 0))

    @staticmethod
    def _set_run_color(run, color_hex: str) -> None:
        try:
            run.font.color.rgb = _hex_to_rgbcolor(color_hex)
        except ValueError:
            pass

    def _apply_text_color(self, run, is_title: bool) -> None:
        """Colour a run for the ground it is actually landing on.

        Body text was previously left at the master's default (black) whatever
        was behind it, so the moment a slide gained a dark background its
        subtitle became black-on-brand-dark. ``self._style`` now tracks the
        *region's* ground while a region-bound block is being drawn, so the
        same reasoning holds when one slide carries two canvases.

        The contrast check is applied here rather than trusted from upstream:
        it is one comparison, it is the last point before the colour reaches
        the file, and an unreadable run is a defect no later pass can see.
        """
        if self._style is None:
            return
        preferred = self._style.title_color if is_title else self._style.body_color
        color_hex = colors.readable_on(self._style.background, preferred)
        try:
            run.font.color.rgb = _hex_to_rgbcolor(color_hex)
        except ValueError:
            pass

    def _bullet_hex(self) -> Optional[str]:
        """The current slide's bullet-glyph colour, if a style is in effect."""
        return self._style.bullet_color if self._style is not None else None

    def _add_chart(
        self,
        slide,
        chart_data: dict,
        pos: PositionConfig,
        slide_title: str = "",
        ground: Optional[str] = None,
    ) -> None:
        """Render chart_data to a design-system-styled PNG and place it on the slide."""
        chart_bytes = _pre_rendered_figure(chart_data) or chart_renderer.render_chart_to_png(
            chart_data, self.design_system, pos.width, pos.height,
            slide_title=slide_title,
            # A chart PNG is transparent, so what it is drawn *on* decides
            # whether its axes and labels are legible. A chart bound to a
            # navy half rendered its axis in the deck's dark tone and came
            # out invisible - the same failure as dark text on a dark
            # ground, in the one element that had no way to know.
            ground=ground or (self._style.background if self._style else None),
        )
        if not chart_bytes:
            return
        img_stream = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        try:
            img_stream.write(chart_bytes)
            img_stream.close()
            picture = slide.shapes.add_picture(
                img_stream.name,
                Inches(pos.left), Inches(pos.top), Inches(pos.width), Inches(pos.height)
            )
            describe_picture(picture, str(chart_data.get("title") or slide_title))
            # Marks this as a picture the render pipeline itself placed, not
            # one already sitting in the template layout (a logo, say) -
            # #187's asset extraction reads this back to know which shapes
            # are actually replaceable content, rather than guessing from
            # shape position, which mispairs the moment a layout's own
            # static pictures come first in document order.
            picture.name = "ts-asset-chart"
        finally:
            os.unlink(img_stream.name)

    def _add_key_metrics(self, slide, metrics_data: dict, pos: PositionConfig) -> None:
        """Draw a row of headline figures: a large number over a short label.

        The one visual a qualitative slide can carry honestly - it invents
        nothing, it just sets figures the deck already states at the size
        their importance deserves. Without it, a slide whose point is "we
        grew 3% and opened 400 stores" renders as two plain bullets.
        """
        items = [
            item for item in (metrics_data.get("items") or [])
            if isinstance(item, dict) and str(item.get("value", "")).strip()
        ]
        if not items:
            return

        style = self._style or self._theme.style_for(deck_theme.ROLE_CONTENT)
        gap = 0.3
        column_width = (pos.width - gap * (len(items) - 1)) / len(items)
        if column_width <= 0:
            return

        # Sized off the column so four figures do not overflow where two fit.
        value_pt = max(24.0, min(56.0, column_width * 12.0))
        label_pt = max(11.0, min(16.0, value_pt * 0.32))
        block_height = (value_pt * 1.25 + label_pt * 2.6) / 72.0
        top = pos.top + max(0.0, pos.height - block_height) / 3.0

        for index, item in enumerate(items):
            left = pos.left + index * (column_width + gap)
            box = slide.shapes.add_textbox(
                Inches(left), Inches(top), Inches(column_width), Inches(block_height)
            )
            frame = box.text_frame
            frame.word_wrap = True
            frame.margin_left = frame.margin_right = 0
            frame.vertical_anchor = MSO_ANCHOR.TOP

            value = frame.paragraphs[0]
            value.alignment = PP_ALIGN.LEFT
            value_run = value.add_run()
            value_run.text = str(item.get("value", "")).strip()
            value_run.font.size = Pt(value_pt)
            value_run.font.bold = True
            value_run.font.name = self._type_scale.title_font
            self._set_run_color(value_run, style.title_color)
            _clear_text_transform(value_run)
            _apply_bullet_formatting(value, False, 0, None)

            label = frame.add_paragraph()
            label.alignment = PP_ALIGN.LEFT
            label_run = label.add_run()
            label_run.text = str(item.get("label", "")).strip()
            label_run.font.size = Pt(label_pt)
            label_run.font.name = self._type_scale.body_font
            self._set_run_color(label_run, style.body_color)
            _clear_text_transform(label_run)
            _apply_bullet_formatting(label, False, 0, None)

            # A short accent rule under each figure, tying the row back to
            # the deck's one repeated motif.
            self._add_accent_rule(
                slide,
                PositionConfig(left=left, top=top + block_height + 0.06,
                               width=min(0.9, column_width * 0.4), height=0.05),
                style.accent_color,
            )

    def _add_table(self, slide, table_data: dict, pos: PositionConfig) -> None:
        """Add a native, design-system-styled table, laid out from its content.

        Drawn by `native_table`, which the composed path uses too, so a table
        is the same editable object whichever path drew it.
        """
        native_table.add_native_table(
            slide, table_data, pos, self.design_system, self._paint_context(),
            id_prefix=f"s{self._slide_number}", placement="optical")

    def _add_text_box(
        self,
        slide,
        runs: list[dict],
        pos: PositionConfig,
        is_title: bool = False,
    ) -> float:
        """Add a text box in the deck's typography. Returns its height in inches.

        Used when the layout supplied no placeholder for a block - a user
        template that omits one, or more content items than the catalog
        declared slots for.
        """
        # PositionConfig fields are inches (plain floats, see layout.py) -
        # python-pptx's shape-creation calls take EMU Length values, and
        # silently treat a raw int/float as EMU rather than raising. Passing
        # pos.left/top/etc. directly - as this used to - produced textboxes a
        # few EMU wide (1 inch = 914400 EMU), i.e. invisible: every slide on
        # the no-template render path had its content positioned into a
        # sub-pixel box. This is the "right slide count, no visible content"
        # bug.
        balance = composition.NO_CHANGE
        if not is_title and runs:
            balance = composition.balance_body(
                [str(r.get("text", "")) for r in runs],
                [self._style_for_run(r, is_title).font_size_pt for r in runs],
                pos.width,
                pos.height,
            )

        top = pos.top + balance.top_offset_in
        height = max(0.1, pos.height - balance.top_offset_in)

        txBox = slide.shapes.add_textbox(
            Inches(pos.left), Inches(top), Inches(pos.width), Inches(height)
        )
        tf = txBox.text_frame
        # Second line of defense: let LibreOffice/PowerPoint's own autofit
        # shrink further at render/open time if our own estimate still
        # undershoots for a given font's real metrics.
        tf.auto_size = MSO_AUTO_SIZE.NONE

        self._write_runs(tf, runs, is_title, pos.width, height)
        return height

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
            picture = slide.shapes.add_picture(
                img_stream.name,
                Inches(pos.left), Inches(pos.top), Inches(pos.width), Inches(pos.height)
            )
            describe_picture(picture, str(img_data.get("altText") or ""))
            # See the matching note in _add_chart - marks this as a
            # pipeline-placed picture for #187's asset extraction.
            picture.name = "ts-asset-image"
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
    ) -> tuple[bytes, list[dict], list[dict]]:
        tree = ET.parse(layout_file)
        root = tree.getroot()
        image_rels: list[dict] = []
        link_rels: list[dict] = []

        def link_rid(url: str) -> str:
            """The relationship id for ``url``, creating one on first use.

            Numbered from LINK_RID_BASE so the sequence cannot collide with the
            image ids, which are allocated in the same pass and whose final
            count is not known until it finishes.
            """
            for rel in link_rels:
                if rel["target"] == url:
                    return rel["rId"]
            rel_id = f"rId{LINK_RID_BASE + len(link_rels)}"
            link_rels.append({"rId": rel_id, "target": url})
            return rel_id

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
                if ph is not None and _placeholder_index(ph) == placeholder_idx:
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
                                a_r = existing_runs[0]
                                a_t = a_r.find("a:t", NS)
                                if a_t is not None:
                                    a_t.text = run_data.get("text", "")
                            else:
                                a_r = ET.SubElement(para, f"{{{NS['a']}}}r")
                                a_t = ET.SubElement(a_r, f"{{{NS['a']}}}t")
                                a_t.text = run_data.get("text", "")
                            _set_paragraph_level(para, int(run_data.get("level", 0) or 0))
                            _apply_run_links(para, a_r, run_data, link_rid)

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
                            _set_paragraph_level(new_para, int(run_data.get("level", 0) or 0))
                            _apply_run_links(new_para, a_r, run_data, link_rid)

                        paragraphs = txBody.findall("a:p", NS)

                    self._apply_autofit_to_placeholder(
                        sp,
                        txBody,
                        paragraphs,
                        self._master_placeholder_boxes(layout_file).get(placeholder_idx),
                    )
                    break

        return (
            ET.tostring(root, encoding="unicode", xml_declaration=True).encode("utf-8"),
            image_rels,
            link_rels,
        )

    def _master_placeholder_boxes(self, layout_file: str) -> dict[int, dict]:
        """Placeholder geometry from the layout's own slide master, by index.

        A layout placeholder may carry no ``<a:xfrm>`` of its own and inherit
        the master's - which is the normal case for stock "Title and Content"
        layouts. The autofit estimate below used to give up on those, so the
        placeholders most likely to be filled with a lot of text were the ones
        that never got a scale hint, and long bodies simply ran off the slide.
        """
        cached = self._master_box_cache.get(layout_file)
        if cached is not None:
            return cached

        boxes: dict[int, dict] = {}
        rels_path = os.path.join(
            os.path.dirname(layout_file),
            "_rels",
            os.path.basename(layout_file) + ".rels",
        )
        try:
            master_target = None
            for rel in ET.parse(rels_path).getroot():
                if rel.get("Type", "").endswith("/slideMaster"):
                    master_target = rel.get("Target")
                    break
            if master_target:
                master_path = os.path.normpath(
                    os.path.join(os.path.dirname(layout_file), master_target)
                )
                master_root = ET.parse(master_path).getroot()
                for sp in master_root.findall(".//p:sp", NS):
                    ph = sp.find(".//p:ph", NS)
                    if ph is None:
                        continue
                    box = get_shape_position(sp)
                    if box["cx"] > 0 and box["cy"] > 0:
                        boxes[_placeholder_index(ph)] = box
        except (OSError, ET.ParseError) as e:
            logger.debug("master_geometry_unavailable", layout=layout_file, error=str(e))

        self._master_box_cache[layout_file] = boxes
        return boxes

    def _apply_autofit_to_placeholder(
        self,
        sp: ET.Element,
        txBody: ET.Element,
        paragraphs: list[ET.Element],
        fallback_box: Optional[dict] = None,
    ) -> None:
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
            # No explicit xfrm on this placeholder: its size comes from the
            # slide master, so judge against that rather than giving up.
            box = fallback_box or box
        if box["cx"] <= 0 or box["cy"] <= 0:
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
        chart_bytes = _pre_rendered_figure(chart_data) or \
            chart_renderer.render_chart_to_png(chart_data, self.design_system,
                                               width_in, height_in)
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

    def _create_slide_rels(
        self,
        working_dir: str,
        slide_num: int,
        layout_id: str,
        image_rels: Optional[list[dict]] = None,
        link_rels: Optional[list[dict]] = None,
    ) -> None:
        rels_dir = os.path.join(working_dir, "ppt", "slides", "_rels")
        os.makedirs(rels_dir, exist_ok=True)
        rels_path = os.path.join(rels_dir, f"slide{slide_num}.xml.rels")

        image_rel_xml = "\n".join(
            f'  <Relationship Id="{rel["rId"]}" '
            f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
            f'Target="{rel["target"]}"/>'
            for rel in (image_rels or [])
        )

        # External targets, so TargetMode="External" - without it PowerPoint
        # reads the URL as a path inside the package and reports the file as
        # corrupt rather than opening the link.
        link_rel_xml = "\n".join(
            f'  <Relationship Id="{rel["rId"]}" '
            f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
            f'Target="{escape(rel["target"], {chr(34): "&quot;"})}" TargetMode="External"/>'
            for rel in (link_rels or [])
        )

        rels_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout"
    Target="../slideLayouts/slideLayout{layout_id}.xml"/>
{image_rel_xml}
{link_rel_xml}
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
        #
        # Matched exactly rather than by substring. The previous test was
        # `"slide" in type and "master" not in type`, and the slide master's
        # relationship type is ".../slideMaster" - capital M - so it never
        # matched the guard and the master relationship was deleted from every
        # template-based render. presentation.xml still pointed at it
        # (<p:sldMasterId r:id="rId1"/>), leaving a dangling reference:
        # LibreOffice refuses the file outright ("source file could not be
        # loaded"), which also meant validate_rendered_output could never
        # inspect a deck built on an uploaded template.
        for rel in list(root):
            if rel.get("Type", "") == SLIDE_REL_TYPE:
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
            rel.set("Type", SLIDE_REL_TYPE)
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

        # Remove existing slide overrides
        for override in list(root):
            part_name = override.get("PartName", "")
            if "/slides/slide" in part_name:
                root.remove(override)

        # Add slide content type overrides
        for i in range(slide_count):
            override = ET.SubElement(root, f"{{{CONTENT_TYPES_NS}}}Override")
            override.set("PartName", f"/ppt/slides/slide{i + 1}.xml")
            override.set(
                "ContentType",
                "application/vnd.openxmlformats-officedocument.presentationml.slide+xml",
            )

        # Written with the content-types namespace as the document default.
        #
        # ElementTree has one global prefix map, and this module registers ""
        # for the *relationships* namespace (which every .rels file needs), so
        # [Content_Types].xml came back out as <ns0:Types><ns0:Default .../>.
        # That is well-formed and namespace-equivalent, and LibreOffice's OPC
        # reader rejects the package outright anyway - "source file could not
        # be loaded" - which is why no deck built on an uploaded template could
        # be opened, previewed, or validated.
        with _default_namespace(CONTENT_TYPES_NS):
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