"""
Turning a DesignDoc page into rectangles.

Two placement systems, and the difference between them is deliberate:

``GridPlace`` resolves against the **page's content box** - the canvas inside
the grid margins - at every depth. A grid is a property of the page, not of
whatever frame happens to contain you, so a caption that says "column 4"
lines up with the chart above it even though the two sit in different
parents. This is what makes alignment survive nesting, and alignment across
unrelated elements is most of what separates a typeset page from a slide.

``FracPlace`` resolves against the parent frame. That is the right frame of
reference for "the lower third of this panel", and the wrong one for
anything that has to line up with something outside the panel.

Bleed is applied last, after the rect is known: a bleeding edge is pushed out
to the trim (and past it, into ``canvas.bleed_in``, when the deck has bleed).
Doing it last means a frame can be placed on the grid like everything else
and still run off the page, which is how publication layouts actually work -
the magenta panel on EY page 4 is a grid column that bleeds right.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Optional

from app.design.schema import (
    Canvas,
    DesignDoc,
    Frame,
    FracPlace,
    GridPlace,
    GridSpec,
    Page,
)
from app.renderer.layout import PositionConfig

# Two frames sharing an edge are touching, not overlapping. Fractional
# geometry rounds, and a hairline between two halves of a split is not a
# defect worth failing a render over.
OVERLAP_TOLERANCE_IN = 0.02
# How far outside the canvas (beyond any declared bleed) a frame may sit
# before it is a mistake rather than an intention.
ESCAPE_TOLERANCE_IN = 0.01


@dataclass(frozen=True)
class ResolvedFrame:
    """A frame with its absolute rectangle and everything inherited into it."""

    frame: Frame
    rect: PositionConfig
    ground: Optional[str]
    z: int
    depth: int
    order: int
    parent_id: Optional[str] = None

    @property
    def id(self) -> str:
        return self.frame.id

    @property
    def right(self) -> float:
        return self.rect.left + self.rect.width

    @property
    def bottom(self) -> float:
        return self.rect.top + self.rect.height


class GeometryError(ValueError):
    """A page whose frames cannot be placed as declared."""


def content_box(canvas: Canvas, grid: GridSpec) -> PositionConfig:
    """The canvas inside the grid's margins - where the columns live."""
    return PositionConfig(
        left=grid.margin.left,
        top=grid.margin.top,
        width=max(0.0, canvas.w_in - grid.margin.left - grid.margin.right),
        height=max(0.0, canvas.h_in - grid.margin.top - grid.margin.bottom),
    )


def _place_on_grid(place: GridPlace, canvas: Canvas, grid: GridSpec) -> PositionConfig:
    box = content_box(canvas, grid)
    col_w = grid.column_width_in(canvas)
    if place.col + place.span > grid.columns:
        raise GeometryError(
            f"columns {place.col}..{place.col + place.span - 1} fall outside "
            f"a {grid.columns}-column grid"
        )
    left = box.left + place.col * (col_w + grid.gutter_in)
    width = place.span * col_w + (place.span - 1) * grid.gutter_in

    if grid.rows and place.row is not None:
        row_span = place.row_span or 1
        if place.row + row_span > grid.rows:
            raise GeometryError(
                f"rows {place.row}..{place.row + row_span - 1} fall outside "
                f"a {grid.rows}-row grid"
            )
        row_h = (
            box.height - grid.row_gutter_in * (grid.rows - 1)
        ) / grid.rows
        top = box.top + place.row * (row_h + grid.row_gutter_in)
        height = row_span * row_h + (row_span - 1) * grid.row_gutter_in
    else:
        # No row system: the frame states its own vertical extent as a
        # fraction of the content box. Defaulting to the full height is what
        # a full-height rail wants and is harmless for anything that states
        # its own.
        y = 0.0 if place.y is None else place.y
        h = 1.0 if place.h is None else place.h
        top = box.top + y * box.height
        height = h * box.height

    return PositionConfig(left=left, top=top, width=width, height=height)


def _place_by_fraction(place: FracPlace, parent: PositionConfig) -> PositionConfig:
    return PositionConfig(
        left=parent.left + place.x * parent.width,
        top=parent.top + place.y * parent.height,
        width=place.w * parent.width,
        height=place.h * parent.height,
    )


def _apply_bleed(rect: PositionConfig, frame: Frame, canvas: Canvas) -> PositionConfig:
    if not frame.bleed:
        return rect
    over = canvas.bleed_in
    left, top = rect.left, rect.top
    right, bottom = rect.left + rect.width, rect.top + rect.height
    if "left" in frame.bleed:
        left = -over
    if "top" in frame.bleed:
        top = -over
    if "right" in frame.bleed:
        right = canvas.w_in + over
    if "bottom" in frame.bleed:
        bottom = canvas.h_in + over
    return PositionConfig(
        left=left, top=top, width=right - left, height=bottom - top
    )


def resolve_page(doc: DesignDoc, page: Page) -> list[ResolvedFrame]:
    """Every frame on the page, in paint order.

    Paint order is ``(z, document order)``. Document order breaks ties so a
    page is deterministic: two frames at the same z paint in the order they
    were written, every time, which is what makes a rendered page a stable
    fixture to measure against.
    """
    grid = doc.grid_for(page)
    canvas = doc.canvas
    page_rect = PositionConfig(left=0.0, top=0.0, width=canvas.w_in, height=canvas.h_in)
    resolved: list[ResolvedFrame] = []
    counter = 0

    def walk(frames, parent_rect: PositionConfig, parent_ground: Optional[str],
             parent_id: Optional[str], depth: int) -> None:
        nonlocal counter
        for frame in frames:
            if isinstance(frame.place, GridPlace):
                rect = _place_on_grid(frame.place, canvas, grid)
            else:
                rect = _place_by_fraction(frame.place, parent_rect)
            rect = _apply_bleed(rect, frame, canvas)
            ground = frame.ground or parent_ground
            resolved.append(
                ResolvedFrame(
                    frame=frame,
                    rect=rect,
                    ground=ground,
                    z=frame.z,
                    depth=depth,
                    order=counter,
                    parent_id=parent_id,
                )
            )
            counter += 1
            walk(frame.children, rect, ground, frame.id, depth + 1)

    walk(page.frames, page_rect, page.ground, None, 0)
    resolved.sort(key=lambda r: (r.z, r.order))
    return resolved


# ── validation ─────────────────────────────────────────────────────────────


def _overlap_in(a: ResolvedFrame, b: ResolvedFrame) -> float:
    """Area of intersection, in square inches."""
    dx = min(a.right, b.right) - max(a.rect.left, b.rect.left)
    dy = min(a.bottom, b.bottom) - max(a.rect.top, b.rect.top)
    if dx <= 0 or dy <= 0:
        return 0.0
    return dx * dy


def check_page(doc: DesignDoc, page: Page) -> list[str]:
    """Everything wrong with this page's geometry, as plain sentences.

    Overlap is an error **only between siblings at equal z**. Type over an
    image, a pull-quote across a panel edge, a figure running under its
    caption - all of these are overlap, all of them are how publication
    pages are built, and the previous validator refused every one of them.
    What remains an error is overlap with no declared order, which is
    ambiguity rather than intent.
    """
    problems: list[str] = []
    canvas = doc.canvas
    resolved = resolve_page(doc, page)

    for item in resolved:
        allowed = canvas.bleed_in + ESCAPE_TOLERANCE_IN
        if item.rect.width <= 0 or item.rect.height <= 0:
            problems.append(
                f"frame {item.id!r} resolves to zero or negative size "
                f"({item.rect.width:.3f} x {item.rect.height:.3f} in)"
            )
            continue
        if not item.frame.bleed and (
            item.rect.left < -allowed
            or item.rect.top < -allowed
            or item.right > canvas.w_in + allowed
            or item.bottom > canvas.h_in + allowed
        ):
            problems.append(
                f"frame {item.id!r} escapes the canvas without declaring a "
                f"bleed edge (left={item.rect.left:.2f} top={item.rect.top:.2f} "
                f"right={item.right:.2f} bottom={item.bottom:.2f} in)"
            )

    by_parent: dict[Optional[str], list[ResolvedFrame]] = {}
    for item in resolved:
        by_parent.setdefault(item.parent_id, []).append(item)

    for siblings in by_parent.values():
        for i, a in enumerate(siblings):
            for b in siblings[i + 1:]:
                if a.z != b.z:
                    continue
                area = _overlap_in(a, b)
                smaller = min(
                    a.rect.width * a.rect.height, b.rect.width * b.rect.height
                )
                if smaller <= 0:
                    continue
                if area / smaller > OVERLAP_TOLERANCE_IN:
                    problems.append(
                        f"frames {a.id!r} and {b.id!r} overlap by "
                        f"{area / smaller:.0%} at the same z={a.z}; give one a "
                        f"different z to say which is in front"
                    )
    return problems


def check_doc(doc: DesignDoc) -> list[str]:
    """Every geometry problem in the deck, page by page."""
    problems: list[str] = []
    for page in doc.pages:
        for problem in check_page(doc, page):
            problems.append(f"page {page.id!r}: {problem}")
    return problems


def iter_resolved(doc: DesignDoc) -> Iterator[tuple[Page, list[ResolvedFrame]]]:
    for page in doc.pages:
        yield page, resolve_page(doc, page)
