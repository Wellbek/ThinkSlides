"""
One frame, one ground (#396).

A frame that carries copy or an exhibit, and declares no ground of its own,
is drawn in ink chosen for the ground under it. When it sits across the edge
between two grounds - a white half and a navy half - there is no such ink:
whatever it is drawn in, one side of it fails. The WTI pack's split-canvas
page did this twice. Its figure frame was snapped to a grid column that
starts half an inch left of the navy panel, so a chart's axis sat on white
and its bars on navy; and a line traced along its foot ran the width of the
page across both halves.

A frame that brings its own ground is exempt: a pull-quote laid across a
panel edge on its own card is how publication pages are built, and it carries
what its type is read against with it. A scrim does not exempt one - it is a
wash at under half opacity, and the two grounds still differ beneath it.

What a frame sits on is what is *visible* under it, not every ground frame
it overlaps: a panel stacked on a full-page panel hides it, and where no
panel is painted the page's own ground shows. So the frame, as drawn (bleed
included), is cut along every ground edge that crosses it, and each cell
takes the topmost ground painted below the frame there, else the page's,
resolved to a colour so that a role and the hex it names are one ground.
With more than ``STRADDLE_SHARE`` of its area on another colour than the
one most of it is on, the frame is seated: cut to the largest rectangle of
cells on its own colour, whose edges are ground edges, and set a gutter in
from each edge the cut moved. A frame that would keep less than
``MIN_KEPT_SHARE`` of its area is not seated but reported, since a sliver
holds nothing; the shipped packs are tested to have none.
"""

from __future__ import annotations

from dataclasses import dataclass
from app.design.geometry import _apply_bleed, _place_by_fraction, _place_on_grid
from app.design.pack import Pack
from app.design.schema import Canvas, FracPlace, Frame, GridPlace, GridSpec
from app.observability.logging import get_logger
from app.renderer.layout import PositionConfig

logger = get_logger(__name__)

# More of a frame's area than this on another colour, and it is seated.
STRADDLE_SHARE = 0.05
# A seat that keeps less of the frame than this is refused, and reported.
MIN_KEPT_SHARE = 0.25


def _placed(frame: Frame, canvas: Canvas, grid: GridSpec) -> PositionConfig:
    """Where the frame's place puts it, before any bleed."""
    page = PositionConfig(left=0.0, top=0.0, width=canvas.w_in, height=canvas.h_in)
    return (_place_on_grid(frame.place, canvas, grid) if isinstance(frame.place, GridPlace)
            else _place_by_fraction(frame.place, page))


def _rect(frame: Frame, canvas: Canvas, grid: GridSpec) -> PositionConfig:
    """Where the frame is drawn: its place, run off any edge it bleeds."""
    return _apply_bleed(_placed(frame, canvas, grid), frame, canvas)


def _inked_for_its_ground(frame: Frame) -> bool:
    """Whether this frame's ink is chosen for the ground under it: it is not
    itself a panel, and it brings no ground of its own."""
    return frame.role != "panel" and not frame.ground


def _colour(value: str, pack: Pack) -> str:
    try:
        return (value if value.startswith("#") else pack.colors.get(value)).upper()
    except KeyError:
        return value.upper()


@dataclass(frozen=True)
class Straddle:
    """A frame on two grounds: the colour it mostly sits on, and the largest
    part of it (as drawn) on that colour."""

    frame: Frame
    colour: str
    keep: PositionConfig
    kept_share: float


def _cells(rect: PositionConfig, grounds, page_colour: str):
    """The frame cut along every ground edge that crosses it, so each cell
    shows one colour: the x and y cuts, and the colour of each cell, rows top
    to bottom. ``grounds`` is in paint order, so the last one holding a
    cell's middle is the one seen there."""
    right, bottom = rect.left + rect.width, rect.top + rect.height
    xs = sorted({rect.left, right} | {x for ground, _ in grounds
                                      for x in (ground.left, ground.left + ground.width)
                                      if rect.left < x < right})
    ys = sorted({rect.top, bottom} | {y for ground, _ in grounds
                                      for y in (ground.top, ground.top + ground.height)
                                      if rect.top < y < bottom})
    rows = []
    for y0, y1 in zip(ys, ys[1:]):
        row = []
        for x0, x1 in zip(xs, xs[1:]):
            x, y = (x0 + x1) / 2, (y0 + y1) / 2
            colour = page_colour
            for ground, ground_colour in grounds:
                if (ground.left <= x <= ground.left + ground.width
                        and ground.top <= y <= ground.top + ground.height):
                    colour = ground_colour
            row.append(colour)
        rows.append(row)
    return xs, ys, rows


def _largest(xs, ys, rows, colour: str) -> tuple[float, float, float, float]:
    """The largest rectangle of cells all of ``colour``, as (left, top,
    right, bottom): the histogram method, row by row, over cells of unequal
    size."""
    columns = len(xs) - 1
    heights = [0.0] * columns
    best, best_area = (xs[0], ys[0], xs[0], ys[0]), 0.0
    for j, row in enumerate(rows):
        tall = ys[j + 1] - ys[j]
        heights = [h + tall if cell == colour else 0.0 for h, cell in zip(heights, row)]
        stack: list[int] = []
        for i in range(columns + 1):
            h = heights[i] if i < columns else 0.0
            while stack and heights[stack[-1]] >= h:
                height = heights[stack.pop()]
                first = stack[-1] + 1 if stack else 0
                area = height * (xs[i] - xs[first])
                if area > best_area:
                    best_area = area
                    best = (xs[first], ys[j + 1] - height, xs[i], ys[j + 1])
            stack.append(i)
    return best


def straddling(frames, pack: Pack, grid: GridSpec) -> list[Straddle]:
    """The frames inked for their ground that sit on two. Top-level frames
    only: a child is placed inside its parent, and moves with it."""
    canvas = pack.canvas
    page_colour = _colour(pack.colors.ground, pack)
    painted = [frame for _, frame in sorted(enumerate(frames),
                                            key=lambda item: (item[1].z, item[0]))]
    found = []
    for frame in frames:
        if not _inked_for_its_ground(frame):
            continue
        rect = _rect(frame, canvas, grid)
        area = rect.width * rect.height
        if area <= 0:
            continue
        grounds = [(_rect(other, canvas, grid), _colour(other.ground, pack))
                   for other in painted if other.ground and other.z < frame.z]
        xs, ys, rows = _cells(rect, grounds, page_colour)
        shares: dict[str, float] = {}
        for j, row in enumerate(rows):
            for i, cell in enumerate(row):
                shares[cell] = shares.get(cell, 0.0) + (
                    (xs[i + 1] - xs[i]) * (ys[j + 1] - ys[j]))
        if len(shares) < 2:
            continue
        # Most area; on a tie, the colour met first, top-left onward.
        met = [cell for row in rows for cell in row]
        own = max(shares, key=lambda colour: (round(shares[colour], 9), -met.index(colour)))
        if area - shares[own] <= STRADDLE_SHARE * area:
            continue
        left, top, right, bottom = _largest(xs, ys, rows, own)
        found.append(Straddle(
            frame, own,
            PositionConfig(left=left, top=top, width=right - left, height=bottom - top),
            (right - left) * (bottom - top) / area))
    return found


def _seat(frame: Frame, keep: PositionConfig, canvas: Canvas, grid: GridSpec) -> Frame:
    """This frame, cut to ``keep`` and a gutter in from each edge the cut moved.

    Placed by fraction from here on: the edge it now has is the ground's, not
    a grid column's. What is cut is the frame's place, not its bled
    rectangle: an edge that was not cut still bleeds, and when binding takes
    the bleed off for an exhibit - a chart keeps inside the margins - the
    place it falls back to is still the margined one. An edge the cut moved
    loses its bleed, and where the frame's own place already sat a gutter
    inside the kept part on that side, it keeps its place there.
    """
    drawn = _rect(frame, canvas, grid)
    placed = _placed(frame, canvas, grid)
    inset = grid.gutter_in
    edges = {"left": placed.left, "top": placed.top,
             "right": placed.left + placed.width, "bottom": placed.top + placed.height}
    cut = set()
    for edge, kept, was, inward in (
            ("left", keep.left, drawn.left, 1),
            ("top", keep.top, drawn.top, 1),
            ("right", keep.left + keep.width, drawn.left + drawn.width, -1),
            ("bottom", keep.top + keep.height, drawn.top + drawn.height, -1)):
        if abs(kept - was) <= 1e-6:
            continue
        cut.add(edge)
        limit = kept + inward * inset
        if (edges[edge] - limit) * inward < 0:
            edges[edge] = limit
    width = edges["right"] - edges["left"]
    height = edges["bottom"] - edges["top"]
    return frame.model_copy(update={
        "place": FracPlace(x=edges["left"] / canvas.w_in, y=edges["top"] / canvas.h_in,
                           w=width / canvas.w_in, h=height / canvas.h_in),
        "bleed": tuple(edge for edge in frame.bleed if edge not in cut),
    })


def seated(pack: Pack) -> Pack:
    """This pack with every frame inked for its ground on one ground, where
    that leaves it room to hold anything."""
    archetypes = []
    changed = False
    for archetype in pack.archetypes:
        grid = archetype.grid or pack.grid
        moves = {}
        for straddle in straddling(archetype.frames, pack, grid):
            if straddle.kept_share < MIN_KEPT_SHARE:
                logger.warning("frame_not_seated", archetype=archetype.id,
                               frame=straddle.frame.id,
                               kept_share=round(straddle.kept_share, 2))
                continue
            moves[straddle.frame.id] = straddle.keep
        if moves:
            changed = True
            archetype = archetype.model_copy(update={"frames": tuple(
                _seat(frame, moves[frame.id], pack.canvas, grid) if frame.id in moves else frame
                for frame in archetype.frames)})
        archetypes.append(archetype)
    return pack.model_copy(update={"archetypes": tuple(archetypes)}) if changed else pack
