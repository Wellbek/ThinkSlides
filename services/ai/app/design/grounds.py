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

So a pack, as it is loaded, has each such frame seated on the ground it
mostly sits on: clipped to that ground's rectangle and set a gutter in from
every edge it was cut at, so its content does not run to the panel's edge.
The share it may have on a second ground before it is seated is
``STRADDLE_SHARE``; less than that is the hairline a traced edge rounds to.
"""

from __future__ import annotations

from typing import Optional

from app.design.geometry import _apply_bleed, _place_by_fraction, _place_on_grid
from app.design.pack import Pack
from app.design.schema import Canvas, FracPlace, Frame, GridPlace, GridSpec
from app.renderer.layout import PositionConfig

# More of a frame's area than this on a second ground, and it is seated.
STRADDLE_SHARE = 0.05


def _placed(frame: Frame, canvas: Canvas, grid: GridSpec) -> PositionConfig:
    """Where the frame's place puts it, before any bleed."""
    page = PositionConfig(left=0.0, top=0.0, width=canvas.w_in, height=canvas.h_in)
    return (_place_on_grid(frame.place, canvas, grid) if isinstance(frame.place, GridPlace)
            else _place_by_fraction(frame.place, page))


def _rect(frame: Frame, canvas: Canvas, grid: GridSpec) -> PositionConfig:
    """Where the frame is drawn: its place, run off any edge it bleeds."""
    return _apply_bleed(_placed(frame, canvas, grid), frame, canvas)


def _overlap(a: PositionConfig, b: PositionConfig) -> float:
    dx = min(a.left + a.width, b.left + b.width) - max(a.left, b.left)
    dy = min(a.top + a.height, b.top + b.height) - max(a.top, b.top)
    return dx * dy if dx > 0 and dy > 0 else 0.0


def _inked_for_its_ground(frame: Frame) -> bool:
    """Whether this frame's ink is chosen for the ground under it: it is not
    itself a panel, and it brings no ground of its own."""
    return frame.role != "panel" and not frame.ground


def straddling(frames, canvas: Canvas, grid: GridSpec) -> list[tuple[Frame, Frame]]:
    """The frames that sit across two grounds, each with the ground frame it
    mostly sits on - on a tie, the one painted first. Top-level frames only:
    a child is placed inside its parent, and moves with it."""
    grounds = [(frame, _rect(frame, canvas, grid)) for frame in frames if frame.ground]
    found = []
    for frame in frames:
        if not _inked_for_its_ground(frame):
            continue
        rect = _rect(frame, canvas, grid)
        area = rect.width * rect.height
        if area <= 0:
            continue
        under: dict[str, float] = {}
        best: Optional[tuple[float, Frame]] = None
        for ground, ground_rect in grounds:
            if ground.z >= frame.z:
                continue
            shared = _overlap(rect, ground_rect)
            if shared <= 0:
                continue
            colour = ground.ground.upper()
            under[colour] = under.get(colour, 0.0) + shared
            if best is None or shared > best[0]:
                best = (shared, ground)
        if best is None or len(under) < 2:
            continue
        own = best[1].ground.upper()
        elsewhere = sum(share for colour, share in under.items() if colour != own)
        if elsewhere > STRADDLE_SHARE * area:
            found.append((frame, best[1]))
    return found


def _seat(frame: Frame, ground: Frame, canvas: Canvas, grid: GridSpec) -> Frame:
    """This frame, clipped to ``ground`` and a gutter in from each cut edge.

    Placed by fraction from here on: the edge it now has is the panel's, not
    a grid column's. What is clipped is the frame's place, not its bled
    rectangle: an edge it bled past and was not cut at still bleeds, and
    when binding takes the bleed off for an exhibit - a chart keeps inside
    the margins - the place it falls back to is still the margined one.
    """
    rect = _placed(frame, canvas, grid)
    bounds = _rect(ground, canvas, grid)
    inset = grid.gutter_in
    edges = {"left": rect.left, "top": rect.top,
             "right": rect.left + rect.width, "bottom": rect.top + rect.height}
    limits = {"left": bounds.left, "top": bounds.top,
              "right": bounds.left + bounds.width, "bottom": bounds.top + bounds.height}
    cut = set()
    for edge in ("left", "top"):
        if edges[edge] < limits[edge]:
            edges[edge] = limits[edge] + inset
            cut.add(edge)
    for edge in ("right", "bottom"):
        if edges[edge] > limits[edge]:
            edges[edge] = limits[edge] - inset
            cut.add(edge)
    width = max(edges["right"] - edges["left"], 0.01)
    height = max(edges["bottom"] - edges["top"], 0.01)
    return frame.model_copy(update={
        "place": FracPlace(x=edges["left"] / canvas.w_in, y=edges["top"] / canvas.h_in,
                           w=width / canvas.w_in, h=height / canvas.h_in),
        "bleed": tuple(edge for edge in frame.bleed if edge not in cut),
    })


def seated(pack: Pack) -> Pack:
    """This pack with every frame inked for its ground on one ground."""
    archetypes = []
    changed = False
    for archetype in pack.archetypes:
        grid = archetype.grid or pack.grid
        moves = {frame.id: ground for frame, ground in
                 straddling(archetype.frames, pack.canvas, grid)}
        if moves:
            changed = True
            archetype = archetype.model_copy(update={"frames": tuple(
                _seat(frame, moves[frame.id], pack.canvas, grid) if frame.id in moves else frame
                for frame in archetype.frames)})
        archetypes.append(archetype)
    return pack.model_copy(update={"archetypes": tuple(archetypes)}) if changed else pack
