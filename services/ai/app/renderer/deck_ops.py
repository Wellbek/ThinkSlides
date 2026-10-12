"""
Deck-wide operations: the typed vocabulary between engine rules and code.

A request nobody planned for - a logo in the corner of every page, a DRAFT
watermark, a footer, a recolour, a link on every product name - used to be
answered by a script written against the rendered file (#376). That worked,
and the probes in `benchmark/reports/2026-10-04-probes-376.md` say what it
cost: every edit needed two model turns to read the deck before writing one,
a recolour reached shapes but not the theme, and an edit survived a re-render
only because the loop ran the script again over whatever the new composition
produced. A script that guarded itself ("the corner is taken") would then
skip, and the change would be gone.

An operation here is data. The run records it, and the render applies every
recorded operation to every deck it writes, so a re-render cannot lose one.
Each overlay is placed against the page's actual shapes on every render:
shrunk when its full size would cover content, left off a page where even
the smallest size would, and reported either way, so the closing message can
say which pages went without and why rather than the deck silently differing
from the request.

Applied to the written file rather than inside composition, deliberately:
the same operation then holds on all three render paths (composed, uploaded
template, stock), and on the pages the renderer itself appends (the Sources
appendix), which composition never sees.
"""
from __future__ import annotations

import copy
import io
import os
import re
from dataclasses import dataclass, field, replace
from typing import Annotated, Literal, Optional, Union

from pydantic import (BaseModel, ConfigDict, Field, TypeAdapter, ValidationError,
                      field_validator, model_validator)

from app.observability.logging import get_logger

logger = get_logger(__name__)

# Every shape an operation adds carries one of these, so a re-application
# removes what the last one drew, and so the overlap check can tell a
# watermark from damage (see validate_rendered_output.BACKGROUND_PREFIX).
OVERLAY_PREFIX = "ts-overlay-op-"
BACKGROUND_PREFIX = "ts-background-op-"

# How far an overlay sits in from the top or bottom edge, at most. A pack's
# own margin is used when it is smaller: the overlay belongs in the margin
# band, where it is clear of the grid's content by construction. Across, it
# sits on the grid's side margin, so a footer ends where the copy ends rather
# than at an unrelated distance from the edge.
EDGE_INSET_IN = 0.3

# The sizes an overlay is tried at before it is left off a page, as shares of
# the size asked for. Below half, a logo stops reading as the logo the user
# supplied and a footer as text, so a page that cannot take half goes without
# and says so.
SHRINK_STEPS = (1.0, 0.75, 0.5)

# Two boxes closer than this are not counted as overlapping: fractional
# geometry rounds to hairlines, and a hairline is not a collision.
TOLERANCE_IN = 0.02

# A shape covering this share of the page is a ground, not content.
GROUND_SHARE = 0.9

WATERMARK_OPACITY = 0.15
WATERMARK_ROTATION_DEG = -30.0

# The contrast an added element keeps against the ground it lands on. Text
# meets WCAG AA for normal text; a mark that carries no words (a bar, a
# rule, a dot) the 3:1 WCAG asks of graphical objects. An accent bar on a
# cover painted in the accent is otherwise simply not there.
TEXT_CONTRAST = 4.5
MARK_CONTRAST = 3.0

# Average advance of a character, in ems, used to size a text overlay's box
# before it is drawn. Generous on purpose: a box a little wide costs nothing
# with wrapping off, and one too narrow is where "DRAF/T" came from.
CHAR_EM = 0.68

# Office's own extension for "draw this link in the text's colour". Without
# it a link takes the theme's hyperlink blue, which on a dark cover is the
# defect the hyperlink probe found. LibreOffice honours it too.
_HLINK_COLOR_EXT = "{A12FA001-AC4F-418D-AE19-62706E023703}"
_HLINK_COLOR_NS = "http://schemas.microsoft.com/office/drawing/2018/hyperlinkcolor"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
_LINK = re.compile(r"^(https?://|mailto:)\S+$")
COLOR_ROLES = ("ink", "muted", "accent", "accent_alt", "ground")


# ── the vocabulary ─────────────────────────────────────────────────────────


def _color(value: str) -> str:
    value = str(value or "").strip()
    if value in COLOR_ROLES or _HEX.match(value):
        return value
    raise ValueError(
        f"{value!r} is not a colour: give a hex value like #1A2B3C or one of "
        + ", ".join(COLOR_ROLES))


def _url(value: str) -> str:
    value = str(value or "").strip()
    if _LINK.match(value):
        return value
    raise ValueError(f"{value!r} is not a link: it has to start with "
                     "http://, https:// or mailto:")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TextElement(_Model):
    """A line of text. ``{page}`` becomes the page number (a live field) and
    ``{pages}`` the page count."""

    type: Literal["text"] = "text"
    text: str = Field(min_length=1, max_length=200)
    size_pt: Optional[float] = Field(default=None, ge=6, le=200)
    color: str = "muted"
    bold: bool = False
    italic: bool = False

    @field_validator("color")
    @classmethod
    def _colour(cls, value: str) -> str:
        return _color(value)


class ImageElement(_Model):
    """A picture the user supplied, by the name it was uploaded under."""

    type: Literal["image"] = "image"
    asset: str = Field(min_length=1)


class QrElement(_Model):
    """A QR code encoding ``data``, and a link to it when it is one."""

    type: Literal["qr"] = "qr"
    data: str = Field(min_length=1, max_length=1000)


class ShapeElement(_Model):
    type: Literal["shape"] = "shape"
    shape: Literal["rect", "ellipse", "line"] = "rect"
    color: str = "accent"

    @field_validator("color")
    @classmethod
    def _colour(cls, value: str) -> str:
        return _color(value)


class ProgressElement(_Model):
    """A bar along an edge, as long as the share of the deck the reader has
    reached on that page."""

    type: Literal["progress"] = "progress"
    color: str = "accent"
    thickness_pt: float = Field(default=4.0, ge=1.0, le=24.0)

    @field_validator("color")
    @classmethod
    def _colour(cls, value: str) -> str:
        return _color(value)


Element = Annotated[
    Union[TextElement, ImageElement, QrElement, ShapeElement, ProgressElement],
    Field(discriminator="type"),
]

Anchor = Literal["top-left", "top", "top-right", "left", "center", "right",
                 "bottom-left", "bottom", "bottom-right"]
PageSet = Literal["all", "all-but-cover", "first", "last"]


class Overlay(_Model):
    """Something added on top of pages, at an anchor rather than coordinates.

    ``layer="watermark"`` is set across the content on purpose: faint, and
    drawn in front rather than behind, because a watermark behind the content
    is hidden by any filled shape in front of it (the probe's "DRA").
    """

    kind: Literal["overlay"] = "overlay"
    id: str = ""
    element: Element
    anchor: Anchor = "top-left"
    pages: Union[PageSet, list[int]] = "all"
    width_in: Optional[float] = Field(default=None, gt=0, le=20)
    height_in: Optional[float] = Field(default=None, gt=0, le=20)
    layer: Literal["front", "watermark"] = "front"
    rotation_deg: Optional[float] = Field(default=None, ge=-360, le=360)
    opacity: Optional[float] = Field(default=None, gt=0, le=1)
    link: Optional[str] = None

    @field_validator("link")
    @classmethod
    def _link(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else _url(value)

    @model_validator(mode="after")
    def _coherent(self) -> "Overlay":
        if isinstance(self.pages, list) and (
                not self.pages or any(p < 1 for p in self.pages)):
            raise ValueError("pages, as a list, names page numbers counted from 1")
        if isinstance(self.element, ProgressElement) and self.anchor not in ("top", "bottom"):
            raise ValueError("a progress bar runs along the top or the bottom edge")
        if self.layer == "watermark" and not isinstance(
                self.element, (TextElement, ImageElement)):
            raise ValueError("a watermark is text or a picture")
        return self


class Recolour(_Model):
    """Every use of one of the deck's colour roles, in a new colour - in the
    theme as well as on the page, so what is drawn later matches."""

    kind: Literal["recolour"] = "recolour"
    id: str = ""
    role: Literal["accent", "accent_alt"] = "accent"
    to: str

    @field_validator("to")
    @classmethod
    def _hex(cls, value: str) -> str:
        value = str(value or "").strip()
        if not _HEX.match(value):
            raise ValueError(f"{value!r} is not a hex colour like #E4572E")
        return value.upper()


class Link(_Model):
    """Every mention of ``phrase`` in the deck's text, linked to ``url``."""

    kind: Literal["link"] = "link"
    id: str = ""
    phrase: str = Field(min_length=2, max_length=120)
    url: str

    @field_validator("url")
    @classmethod
    def _link(cls, value: str) -> str:
        return _url(value)


Operation = Union[Overlay, Recolour, Link]
_ADAPTER: TypeAdapter = TypeAdapter(
    Annotated[Operation, Field(discriminator="kind")])


def parse(raw: dict) -> Operation:
    """One operation from its JSON form. Raises ``ValueError`` saying what is
    wrong with it, in terms the model can act on."""
    try:
        return _ADAPTER.validate_python(raw)
    except ValidationError as exc:
        reasons = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'operation'}: {error['msg']}"
            for error in exc.errors()[:6])
        raise ValueError(reasons) from None


# ── what an application needs to know about the deck ──────────────────────


@dataclass(frozen=True)
class DeckStyle:
    """The deck's own colours, face and margin, for an operation to wear."""

    ink: str = "#1A1A1A"
    ink_reversed: str = "#FFFFFF"
    muted: str = "#6B6B6B"
    accent: str = "#1F4E79"
    accent_alt: str = "#C55A11"
    ground: str = "#FFFFFF"
    face: str = "Calibri"
    small_pt: float = 10.0
    margin_in: float = 0.5
    # The grid's side margin: where an overlay's left or right edge sits.
    side_in: float = 0.5
    # Rendered page numbers, from 1, that are covers - for "all-but-cover".
    cover_pages: frozenset[int] = frozenset({1})
    # Colours on the page that are a role's previous value, repainted when
    # that role is recoloured: what the pack or the template drew it in.
    previous: dict[str, frozenset[str]] = field(default_factory=dict)

    def resolve(self, color: str, *, on_dark: bool = False) -> str:
        if color.startswith("#"):
            return color
        if color in ("ink", "muted") and on_dark:
            return self.ink_reversed
        return {"ink": self.ink, "muted": self.muted, "accent": self.accent,
                "accent_alt": self.accent_alt, "ground": self.ground}[color]


def recoloured(style: DeckStyle, operations) -> DeckStyle:
    """The style with every recolour applied, remembering what it replaced."""
    previous = {role: set(values) for role, values in style.previous.items()}
    updates: dict[str, str] = {}
    for op in operations:
        if isinstance(op, Recolour):
            previous.setdefault(op.role, set()).add(getattr(style, op.role).upper())
            updates[op.role] = op.to
    if not updates:
        return style
    return replace(style, **updates, previous={
        role: frozenset(values) for role, values in previous.items()})


# ── geometry ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Box:
    left: float
    top: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.left + self.width

    @property
    def bottom(self) -> float:
        return self.top + self.height

    def overlaps(self, other: "Box") -> bool:
        return (self.left < other.right - TOLERANCE_IN
                and other.left < self.right - TOLERANCE_IN
                and self.top < other.bottom - TOLERANCE_IN
                and other.top < self.bottom - TOLERANCE_IN)

    def contains_point(self, x: float, y: float) -> bool:
        return self.left <= x <= self.right and self.top <= y <= self.bottom


def anchored(anchor: str, width: float, height: float, page_w: float,
             page_h: float, inset: float, side: Optional[float] = None) -> Box:
    """A box of this size at this anchor, ``inset`` in from the top and
    bottom edges and ``side`` (``inset`` unless given) from the left and
    right."""
    side = inset if side is None else side
    vertical, _, horizontal = anchor.partition("-")
    if not horizontal:
        # "top", "left", "center": one word names one axis, the other centres.
        horizontal = vertical if vertical in ("left", "right") else "center"
        vertical = vertical if vertical in ("top", "bottom") else "center"
    left = {"left": side, "right": page_w - side - width}.get(
        horizontal, (page_w - width) / 2)
    top = {"top": inset, "bottom": page_h - inset - height}.get(
        vertical, (page_h - height) / 2)
    return Box(left, top, width, height)


def pages_for(spec, count: int, covers: frozenset[int]) -> list[int]:
    """The page numbers, from 1, that an overlay's ``pages`` names in a deck
    of ``count`` pages."""
    if isinstance(spec, list):
        return sorted({page for page in spec if 1 <= page <= count})
    if spec == "first":
        return [1] if count else []
    if spec == "last":
        return [count] if count else []
    if spec == "all-but-cover":
        return [page for page in range(1, count + 1) if page not in covers]
    return list(range(1, count + 1))


# ── applying them ──────────────────────────────────────────────────────────


def apply(path: str, operations, style: DeckStyle,
          assets: Optional[dict[str, bytes]] = None) -> list[dict]:
    """Apply every operation to the deck at ``path``, in place.

    Returns one report per operation: the pages it landed on, the pages it
    was left off and why, and a problem when it could not be applied at all.
    A failure costs that operation only, and the deck is written whole.
    Recolours go first so an overlay drawn in the accent wears the new one,
    and links before overlays so a footer's own words are not linked.
    """
    from pptx import Presentation

    ops = [parse(op) if isinstance(op, dict) else op for op in operations]
    if not ops:
        return []
    presentation = Presentation(path)
    style = recoloured(style, ops)
    reports: list[dict] = []
    for kind, work in (
        (Recolour, lambda op: _apply_recolour(presentation, op, style)),
        (Link, lambda op: _apply_link(presentation, op)),
        (Overlay, lambda op: _apply_overlay(presentation, op, style, assets or {})),
    ):
        for op in ops:
            if isinstance(op, kind):
                reports.append(_guarded(op, work))

    temporary = f"{path}.ops"
    presentation.save(temporary)
    os.replace(temporary, path)
    logger.info("deck_operations_applied", operations=len(ops),
                skipped=sum(len(r.get("skipped") or ()) for r in reports),
                problems=sum(1 for r in reports if r.get("problem")))
    return reports


def _guarded(op: Operation, work) -> dict:
    report: dict = {"id": op.id, "kind": op.kind}
    try:
        report.update(work(op) or {})
    except Exception as exc:   # noqa: BLE001 - one operation, not the deck
        logger.warning("deck_operation_failed", id=op.id, kind=op.kind, error=str(exc))
        report["problem"] = str(exc)[:300]
    return report


def _inches(emu) -> float:
    return float(emu or 0) / 914400.0


def _box_of(shape) -> Optional[Box]:
    if None in (shape.left, shape.top, shape.width, shape.height):
        return None
    return Box(_inches(shape.left), _inches(shape.top),
               _inches(shape.width), _inches(shape.height))


def _ours(shape) -> bool:
    name = shape.name or ""
    return name.startswith(OVERLAY_PREFIX) or name.startswith(BACKGROUND_PREFIX)


def _has_text(shape) -> bool:
    return bool(getattr(shape, "has_text_frame", False) and shape.text_frame.text.strip())


def _is_obstacle(shape, page: Box) -> bool:
    """Whether an overlay laid over this shape would cover something.

    Text, pictures, tables, charts and groups are content. A filled or
    outlined shape with no text is a panel or a rule: an overlay may sit on
    it, as type sits on a panel, and its colour is then read off it.
    """
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    if (shape.name or "").startswith("ts-background"):
        return False
    box = _box_of(shape)
    if box is None or box.width * box.height >= GROUND_SHARE * page.width * page.height:
        return False
    if shape.shape_type in (MSO_SHAPE_TYPE.PICTURE, MSO_SHAPE_TYPE.GROUP,
                            MSO_SHAPE_TYPE.TABLE, MSO_SHAPE_TYPE.CHART,
                            MSO_SHAPE_TYPE.EMBEDDED_OLE_OBJECT):
        return True
    if getattr(shape, "has_chart", False) or getattr(shape, "has_table", False):
        return True
    return _has_text(shape)


def _solid_fill(fill) -> Optional[str]:
    from pptx.enum.dml import MSO_FILL

    try:
        if fill.type == MSO_FILL.SOLID:
            return "#" + str(fill.fore_color.rgb)
    except Exception:   # noqa: BLE001 - a theme colour or gradient is not a ground we can read
        return None
    return None


def _ground_at(slide, box: Box, default: str) -> str:
    """The colour behind the middle of ``box``: the topmost solid shape there
    without text of its own, else the slide's background."""
    x, y = box.left + box.width / 2, box.top + box.height / 2
    ground = None
    for shape in slide.shapes:
        if _ours(shape) or (shape.name or "").startswith("ts-background") or _has_text(shape):
            continue
        shape_box = _box_of(shape)
        if shape_box is None or not shape_box.contains_point(x, y):
            continue
        colour = _solid_fill(shape.fill) if hasattr(shape, "fill") else None
        if colour:
            ground = colour
    if ground:
        return ground
    try:
        return _solid_fill(slide.background.fill) or default
    except Exception:   # noqa: BLE001
        return default


def _apply_overlay(presentation, op: Overlay, style: DeckStyle,
                   assets: dict[str, bytes]) -> dict:
    slides = list(presentation.slides)
    page = Box(0.0, 0.0, _inches(presentation.slide_width),
               _inches(presentation.slide_height))
    picture = _picture_bytes(op.element, assets)
    size_pt = _text_size(op, style, page) if isinstance(op.element, TextElement) else 0.0
    width, height = _natural_size(op, style, page, picture, size_pt)
    inset = (min(EDGE_INSET_IN, style.margin_in), style.side_in)

    placed: list[int] = []
    skipped: list[dict] = []
    for number in pages_for(op.pages, len(slides), style.cover_pages):
        slide = slides[number - 1]
        _remove(slide, op.id)
        if isinstance(op.element, ProgressElement):
            box = anchored(op.anchor, width, height, page.width, page.height, 0.0, 0.0)
            box = Box(0.0, box.top, page.width * number / len(slides), height)
        else:
            box = _clear_box(slide, op, width, height, page, inset)
        if box is None:
            skipped.append({"page": number,
                            "reason": _why_not(slide, op, width, height, page, inset)})
            continue
        scale = box.height / height if height else 1.0
        _draw(slide, op, box, style, _ground_at(slide, box, style.ground),
              picture, size_pt * scale, number, len(slides))
        placed.append(number)
    report: dict = {"pages": placed, "skipped": skipped}
    if op.pages == "all-but-cover" and not style.cover_pages & set(range(1, len(slides) + 1)):
        # Said, because the request named a cover the deck does not have: a
        # short deck opens on content (#347), and the reader should know the
        # element is on the first page too, and why.
        report["note"] = "the deck has no cover, so it is on every page"
    return report


def _obstacles(slide, page: Box) -> list[Box]:
    # Overlays this render already put on the page count too, so two
    # operations anchored to the same corner do not stack.
    return [box for box in (_box_of(shape) for shape in slide.shapes
                            if _ours(shape) or _is_obstacle(shape, page)) if box]


def _clear_box(slide, op: Overlay, width: float, height: float, page: Box,
               inset: tuple[float, float]) -> Optional[Box]:
    """The largest tried size at the overlay's anchor that covers no
    content, or None."""
    if op.layer == "watermark":
        return anchored(op.anchor, width, height, page.width, page.height, *inset)
    obstacles = _obstacles(slide, page)
    for step in SHRINK_STEPS:
        box = anchored(op.anchor, width * step, height * step,
                       page.width, page.height, *inset)
        if not any(box.overlaps(other) for other in obstacles):
            return box
    return None


def _why_not(slide, op: Overlay, width: float, height: float, page: Box,
             inset: tuple[float, float]) -> str:
    """Why an overlay could not go on this page, naming what it would cover."""
    step = SHRINK_STEPS[-1]
    box = anchored(op.anchor, width * step, height * step, page.width, page.height, *inset)
    for shape in slide.shapes:
        other = _box_of(shape)
        if other is None or not box.overlaps(other):
            continue
        if _ours(shape):
            return f"another added element already sits at the {op.anchor}"
        if _is_obstacle(shape, page):
            text = shape.text_frame.text.strip() if _has_text(shape) else ""
            what = f"the text '{text[:40]}'" if text else "a picture, table or chart"
            return f"even at half size it would cover {what} at the {op.anchor}"
    return f"no clear space at the {op.anchor}"


def _picture_bytes(element, assets: dict[str, bytes]) -> Optional[bytes]:
    if isinstance(element, ImageElement):
        data = assets.get(element.asset)
        if data is None:
            names = ", ".join(sorted(assets)) or "none were supplied"
            raise ValueError(f"there is no picture named {element.asset!r} "
                             f"(the user's pictures: {names})")
        return data
    if isinstance(element, QrElement):
        import segno

        buffer = io.BytesIO()
        segno.make(element.data, error="m").save(
            buffer, kind="png", scale=12, border=4, dark="#000000", light="#FFFFFF")
        return buffer.getvalue()
    return None


def _text_size(op: Overlay, style: DeckStyle, page: Box) -> float:
    element = op.element
    if element.size_pt:
        return element.size_pt
    if op.layer == "watermark":
        # Large enough to read as a mark across the page: the line runs about
        # two thirds of the page's diagonal, at about 0.6 em per character.
        diagonal = (page.width ** 2 + page.height ** 2) ** 0.5
        return max(24.0, min(160.0, 0.66 * diagonal * 72 / (CHAR_EM * max(1, len(element.text)))))
    return style.small_pt


def _natural_size(op: Overlay, style: DeckStyle, page: Box,
                  picture: Optional[bytes], size_pt: float) -> tuple[float, float]:
    element = op.element
    if isinstance(element, ProgressElement):
        return page.width, element.thickness_pt / 72.0
    if isinstance(element, TextElement):
        sample = element.text.replace("{page}", "00").replace("{pages}", "00")
        natural = len(sample) * size_pt * CHAR_EM / 72.0 + 0.1
        if op.layer != "watermark":
            natural = min(page.width - 2 * style.margin_in, natural)
        width = op.width_in or natural
        return width, op.height_in or size_pt * 1.3 / 72.0
    if picture is not None:
        from PIL import Image

        with Image.open(io.BytesIO(picture)) as image:
            aspect = image.width / max(1, image.height)
        if op.width_in and op.height_in:
            # Fitted inside the box asked for, never stretched.
            width = min(op.width_in, op.height_in * aspect)
            return width, width / aspect
        if op.width_in:
            return op.width_in, op.width_in / aspect
        height = op.height_in or (1.0 if isinstance(element, QrElement) else 0.6)
        return height * aspect, height
    line = isinstance(element, ShapeElement) and element.shape == "line"
    return op.width_in or 0.5, op.height_in or (0.02 if line else 0.5)


def _name(op: Overlay, number: int) -> str:
    prefix = BACKGROUND_PREFIX if op.layer == "watermark" else OVERLAY_PREFIX
    return f"{prefix}{op.id}-{number}"


def _remove(slide, op_id: str) -> None:
    for shape in list(slide.shapes):
        name = shape.name or ""
        if any(name.startswith(f"{prefix}{op_id}-")
               for prefix in (OVERLAY_PREFIX, BACKGROUND_PREFIX)):
            shape._element.getparent().remove(shape._element)


def _draw(slide, op: Overlay, box: Box, style: DeckStyle, ground: str,
          picture: Optional[bytes], size_pt: float, number: int, count: int) -> None:
    from pptx.util import Inches

    from app import colors as colors_module

    on_dark = colors_module.is_dark(ground)

    def colour(role: str, minimum: float) -> str:
        return colors_module.readable_on(
            ground, style.resolve(role, on_dark=on_dark),
            (style.ink_reversed if on_dark else style.ink,), minimum)

    opacity = op.opacity if op.opacity is not None else (
        WATERMARK_OPACITY if op.layer == "watermark" else 1.0)
    rotation = op.rotation_deg if op.rotation_deg is not None else (
        WATERMARK_ROTATION_DEG if op.layer == "watermark" else 0.0)
    element = op.element
    position = (Inches(box.left), Inches(box.top), Inches(box.width), Inches(box.height))

    if picture is not None:
        shape = slide.shapes.add_picture(io.BytesIO(picture), *position)
        if opacity < 1.0:
            _picture_alpha(shape, opacity)
        link = op.link or (element.data if isinstance(element, QrElement)
                           and _LINK.match(element.data) else None)
        if link:
            shape.click_action.hyperlink.address = link
        shape._element.nvPicPr.cNvPr.set(
            "descr", f"QR code: {element.data}" if isinstance(element, QrElement)
            else element.asset)
    elif isinstance(element, TextElement):
        shape = slide.shapes.add_textbox(*position)
        _set_text(shape, op, element, style, colour(element.color, TEXT_CONTRAST),
                  opacity, size_pt, number, count)
    else:
        shape = _add_shape(slide, element, box, colour(element.color, MARK_CONTRAST), opacity)
    shape.name = _name(op, number)
    if rotation:
        shape.rotation = rotation


def _set_text(shape, op: Overlay, element: TextElement, style: DeckStyle,
              colour: str, opacity: float, size_pt: float, number: int,
              count: int) -> None:
    from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN

    frame = shape.text_frame
    # Neither wrapped nor resized: the box was sized from the text, and
    # LibreOffice wraps a rotated auto-fit box even with wrapping off.
    frame.word_wrap = False
    frame.auto_size = MSO_AUTO_SIZE.NONE
    frame.margin_left = frame.margin_right = frame.margin_top = frame.margin_bottom = 0
    vertical, _, horizontal = op.anchor.partition("-")
    horizontal = horizontal or vertical
    frame.vertical_anchor = {"top": MSO_ANCHOR.TOP, "bottom": MSO_ANCHOR.BOTTOM}.get(
        vertical, MSO_ANCHOR.MIDDLE)
    paragraph = frame.paragraphs[0]
    paragraph.alignment = {"left": PP_ALIGN.LEFT, "right": PP_ALIGN.RIGHT}.get(
        horizontal, PP_ALIGN.CENTER)

    for piece in re.split(r"(\{page\}|\{pages\})", element.text):
        if not piece:
            continue
        if piece == "{page}":
            run = _slide_number_field(paragraph, str(number))
        else:
            added = paragraph.add_run()
            added.text = str(count) if piece == "{pages}" else piece
            run = added._r
        _style_run(run, style.face, size_pt, colour, element.bold, element.italic, opacity)
        if op.link and piece != "{page}":
            from pptx.opc.constants import RELATIONSHIP_TYPE as RT

            _link_run(run, shape.part.relate_to(op.link, RT.HYPERLINK, is_external=True))


def _slide_number_field(paragraph, text: str):
    from pptx.oxml import parse_xml
    from pptx.oxml.ns import nsdecls

    field_xml = parse_xml(
        f'<a:fld {nsdecls("a")} id="{{B6F15528-21DE-4FAA-801E-634DDDAF4B2B}}" '
        f'type="slidenum"><a:rPr lang="en-US"/><a:t>{text}</a:t></a:fld>')
    end = paragraph._p.find(f"{_A}endParaRPr")
    if end is not None:
        end.addprevious(field_xml)
    else:
        paragraph._p.append(field_xml)
    return field_xml


def _style_run(run, face: str, size_pt: float, colour: str, bold: bool,
               italic: bool, opacity: float) -> None:
    from pptx.oxml import parse_xml
    from pptx.oxml.ns import nsdecls

    rPr = run.find(f"{_A}rPr")
    if rPr is None:
        rPr = parse_xml(f'<a:rPr {nsdecls("a")} lang="en-US"/>')
        run.insert(0, rPr)
    rPr.set("sz", str(int(round(size_pt * 100))))
    if bold:
        rPr.set("b", "1")
    if italic:
        rPr.set("i", "1")
    alpha = f'<a:alpha val="{int(opacity * 100000)}"/>' if opacity < 1.0 else ""
    rPr.append(parse_xml(
        f'<a:solidFill {nsdecls("a")}><a:srgbClr val="{colour.lstrip("#").upper()}">'
        f'{alpha}</a:srgbClr></a:solidFill>'))
    face_xml = (face.replace("&", "&amp;").replace('"', "&quot;")
                .replace("<", "&lt;").replace(">", "&gt;"))
    rPr.append(parse_xml(f'<a:latin {nsdecls("a")} typeface="{face_xml}"/>'))


def _add_shape(slide, element, box: Box, colour: str, opacity: float):
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
    from pptx.oxml import parse_xml
    from pptx.oxml.ns import nsdecls
    from pptx.util import Inches, Pt

    rgb = RGBColor.from_string(colour.lstrip("#").upper())
    if isinstance(element, ShapeElement) and element.shape == "line":
        middle = Inches(box.top + box.height / 2)
        shape = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(box.left),
                                           middle, Inches(box.right), middle)
        shape.line.color.rgb = rgb
        shape.line.width = Pt(max(0.75, box.height * 72))
        return shape
    kind = (MSO_SHAPE.OVAL if isinstance(element, ShapeElement) and element.shape == "ellipse"
            else MSO_SHAPE.RECTANGLE)
    shape = slide.shapes.add_shape(kind, Inches(box.left), Inches(box.top),
                                   Inches(box.width), Inches(box.height))
    shape.fill.solid()
    shape.fill.fore_color.rgb = rgb
    if opacity < 1.0:
        shape._element.spPr.find(f"{_A}solidFill/{_A}srgbClr").append(
            parse_xml(f'<a:alpha {nsdecls("a")} val="{int(opacity * 100000)}"/>'))
    shape.line.fill.background()
    shape.shadow.inherit = False
    return shape


def _picture_alpha(shape, opacity: float) -> None:
    from pptx.oxml import parse_xml
    from pptx.oxml.ns import nsdecls

    blip = shape._element.find(f".//{_A}blip")
    if blip is not None:
        blip.insert(0, parse_xml(
            f'<a:alphaModFix {nsdecls("a")} amt="{int(opacity * 100000)}"/>'))


# ── recolour ───────────────────────────────────────────────────────────────

_THEME_SLOT = {"accent": "accent1", "accent_alt": "accent2"}


def _apply_recolour(presentation, op: Recolour, style: DeckStyle) -> dict:
    """Repaint the role's previous colours on every page and the theme's slot
    for it, so a shape drawn from the theme later wears the new colour too."""
    from lxml import etree
    from pptx.opc.constants import RELATIONSHIP_TYPE as RT

    old = {value.lstrip("#").upper() for value in style.previous.get(op.role, ())}
    new = op.to.lstrip("#").upper()
    slot = _THEME_SLOT[op.role]
    for master in presentation.slide_masters:
        try:
            theme = master.part.part_related_by(RT.THEME)
        except KeyError:
            continue
        root = etree.fromstring(theme.blob)
        for element in root.iter(f"{_A}{slot}"):
            colour = element.find(f"{_A}srgbClr")
            if colour is not None:
                old.add(colour.get("val", "").upper())
                colour.set("val", new)
        theme._blob = etree.tostring(root, xml_declaration=True,
                                     encoding="UTF-8", standalone=True)
    old.discard(new)
    old.discard("")

    parts = [slide.part for slide in presentation.slides]
    for master in presentation.slide_masters:
        parts.append(master.part)
        parts.extend(layout.part for layout in master.slide_layouts)
    repainted = 0
    for part in parts:
        for element in part._element.iter(f"{_A}srgbClr"):
            if element.get("val", "").upper() in old:
                element.set("val", new)
                repainted += 1
    return {"pages": list(range(1, len(presentation.slides) + 1)), "repainted": repainted}


# ── links ──────────────────────────────────────────────────────────────────


def _apply_link(presentation, op: Link) -> dict:
    from pptx.opc.constants import RELATIONSHIP_TYPE as RT

    pattern = re.compile(re.escape(op.phrase), re.IGNORECASE)
    pages: list[int] = []
    linked = 0
    for number, slide in enumerate(presentation.slides, start=1):
        relation: list[str] = []

        def relate(part=slide.part, relation=relation) -> str:
            if not relation:
                relation.append(part.relate_to(op.url, RT.HYPERLINK, is_external=True))
            return relation[0]

        count = sum(_link_paragraph(paragraph, pattern, relate)
                    for paragraph in list(slide._element.iter(f"{_A}p")))
        if count:
            pages.append(number)
            linked += count
    if not linked:
        return {"pages": [], "problem": f"the deck never mentions {op.phrase!r}"}
    return {"pages": pages, "linked": linked}


def _runs(paragraph) -> list:
    return [child for child in paragraph if child.tag == f"{_A}r"]


def _link_paragraph(paragraph, pattern, relate) -> int:
    """Link every match of ``pattern`` in one paragraph, splitting runs at the
    match's edges so only the phrase itself is linked. Returns how many."""
    text = "".join(run.findtext(f"{_A}t") or "" for run in _runs(paragraph))
    matches = [(m.start(), m.end()) for m in pattern.finditer(text)]
    for start, end in matches:
        _split_at(paragraph, start)
        _split_at(paragraph, end)
    done = 0
    for start, end in matches:
        inside, offset = [], 0
        for run in _runs(paragraph):
            length = len(run.findtext(f"{_A}t") or "")
            if length and start <= offset and offset + length <= end:
                inside.append(run)
            offset += length
        if not inside or any(_linked(run) for run in inside):
            continue
        rId = relate()
        for run in inside:
            _link_run(run, rId)
        done += 1
    return done


def _split_at(paragraph, position: int) -> None:
    offset = 0
    for run in _runs(paragraph):
        t = run.find(f"{_A}t")
        text = (t.text or "") if t is not None else ""
        if offset < position < offset + len(text):
            tail = copy.deepcopy(run)
            t.text = text[:position - offset]
            tail.find(f"{_A}t").text = text[position - offset:]
            run.addnext(tail)
            return
        offset += len(text)


def _linked(run) -> bool:
    rPr = run.find(f"{_A}rPr")
    return rPr is not None and rPr.find(f"{_A}hlinkClick") is not None


def _link_run(run, rId: str) -> None:
    """Link one run, underlined and in its own colour."""
    from pptx.oxml import parse_xml
    from pptx.oxml.ns import nsdecls

    rPr = run.find(f"{_A}rPr")
    if rPr is None:
        rPr = parse_xml(f'<a:rPr {nsdecls("a")} lang="en-US"/>')
        run.insert(0, rPr)
    rPr.set("u", "sng")
    click = parse_xml(
        f'<a:hlinkClick {nsdecls("a", "r")} r:id="{rId}"><a:extLst>'
        f'<a:ext uri="{_HLINK_COLOR_EXT}"><ahyp:hlinkClr xmlns:ahyp="{_HLINK_COLOR_NS}" '
        f'val="tx"/></a:ext></a:extLst></a:hlinkClick>')
    # Where the schema wants it: after fills, effects and faces, before
    # hlinkMouseOver, rtl and extLst.
    later = {f"{_A}hlinkMouseOver", f"{_A}rtl", f"{_A}extLst"}
    following = next((child for child in rPr if child.tag in later), None)
    if following is not None:
        following.addprevious(click)
    else:
        rPr.append(click)
