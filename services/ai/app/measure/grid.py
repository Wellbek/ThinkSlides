"""
Recovering the column grid a publication is set on.

A grid is not written down anywhere in a PDF. What is written down is where
every element starts and stops, and a page set on a grid puts those edges in
the same few places over and over. So the grid is recoverable as a *period*:
find the pitch that most of the left edges are a whole number of steps apart
on, then read the gutter off the right edges.

The hard part is not finding a grid. It is refusing to.

Period estimation always returns an answer. Ask it for the pitch of a page
laid out by hand and it will hand back a confident number that explains
nothing, and every later stage will then measure conformance to a fiction and
report progress. So this reports ``edge_snap`` - the share of edge mass that
actually lands on the recovered tracks - and callers are expected to treat a
low value as "this deck has no grid" rather than as a grid to try harder
with. ``recover_grid`` returns ``None`` below the threshold, on purpose.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

# A pitch narrower than this would "explain" every edge by having a track
# almost everywhere, which is the degenerate answer period estimation loves.
MIN_PITCH = 0.035
MAX_PITCH = 0.5
PITCH_STEP = 0.0005
# An edge within this fraction of a track counts as sitting on it.
SNAP_TOLERANCE = 0.006
# Below this share of edge mass on-track, we report no grid at all.
MIN_EDGE_SNAP = 0.6
# What each additional column has to earn, as a share of edge mass, before
# it is believed. Publication grids are coarse - 4, 6, 8, 12 - and a page
# with a dozen elements on it is not evidence for a 24-column grid.
COLUMN_CHARGE = 0.02
MAX_COLUMNS = 16


@dataclass(frozen=True)
class Edge:
    """One observed element boundary, weighted by how much page it accounts for."""

    position: float
    weight: float = 1.0
    side: str = "left"


@dataclass(frozen=True)
class RecoveredGrid:
    """A column grid, and how much of the evidence it actually explains."""

    columns: int
    margin_left: float
    margin_right: float
    column_width: float
    gutter: float
    edge_snap: float
    residual: float
    pitch: float
    considered: tuple[int, ...] = field(default=())

    @property
    def trustworthy(self) -> bool:
        return self.edge_snap >= MIN_EDGE_SNAP

    def tracks(self) -> list[tuple[float, float]]:
        """(left, right) of every column, as fractions of page width."""
        return [
            (
                self.margin_left + i * self.pitch,
                self.margin_left + i * self.pitch + self.column_width,
            )
            for i in range(self.columns)
        ]

    def snap(self, position: float) -> tuple[int, float]:
        """The nearest track edge, and how far away it was."""
        best_index, best_distance = 0, float("inf")
        for index, (left, right) in enumerate(self.tracks()):
            for candidate in (left, right):
                distance = abs(position - candidate)
                if distance < best_distance:
                    best_index, best_distance = index, distance
        return best_index, best_distance


def _lattice_residual(positions: Sequence[float], weights: Sequence[float],
                      pitch: float, origin: float) -> float:
    """Mean weighted distance from each position to the nearest lattice point."""
    total_weight = sum(weights) or 1.0
    total = 0.0
    for position, weight in zip(positions, weights):
        offset = (position - origin) / pitch
        distance = abs(offset - round(offset)) * pitch
        total += distance * weight
    return total / total_weight


def _best_origin(positions: Sequence[float], weights: Sequence[float],
                 pitch: float) -> float:
    """The lattice phase that best explains these positions, circularly."""
    sin_sum = cos_sum = 0.0
    for position, weight in zip(positions, weights):
        angle = 2.0 * math.pi * (position / pitch)
        sin_sum += weight * math.sin(angle)
        cos_sum += weight * math.cos(angle)
    if sin_sum == 0.0 and cos_sum == 0.0:
        return min(positions) if positions else 0.0
    phase = math.atan2(sin_sum, cos_sum) / (2.0 * math.pi)
    origin = phase * pitch
    while origin < 0:
        origin += pitch
    return origin


def recover_grid(edges: Iterable[Edge]) -> Optional[RecoveredGrid]:
    """The grid these edges imply, or None when they do not imply one.

    Returning None is the important behaviour. An invented grid is worse
    than an admitted absence: everything downstream would measure
    conformance to it, and report improvement as the pages converged on
    something the publication never used.
    """
    edges = [e for e in edges if 0.0 <= e.position <= 1.0]
    lefts = [e for e in edges if e.side == "left"]
    rights = [e for e in edges if e.side == "right"]
    if len(lefts) < 3:
        return None

    left_pos = [e.position for e in lefts]
    left_w = [e.weight for e in lefts]

    # Maximise the share of edge mass that lands on a track, rather than
    # minimising mean distance. A publication page has a handful of elements
    # deliberately off the grid - an indented rail, a folio hung in the
    # margin - and a least-squares fit lets those few drag the whole lattice
    # off the many that are on it. Counting hits is indifferent to how far
    # away an outlier is, which is the property that matters here.
    total_left = sum(left_w) or 1.0
    steps = int((MAX_PITCH - MIN_PITCH) / PITCH_STEP)
    candidate_origins = sorted({round(p, 4) for p in left_pos})

    per_pitch: list[tuple[float, float, float, float]] = []
    for step in range(steps + 1):
        pitch = MIN_PITCH + step * PITCH_STEP
        best_here = None
        for origin_seed in candidate_origins:
            origin = origin_seed % pitch
            hit = 0.0
            for position, weight in zip(left_pos, left_w):
                offset = (position - origin) / pitch
                if abs(offset - round(offset)) * pitch <= SNAP_TOLERANCE:
                    hit += weight
            if best_here is None or hit > best_here[0]:
                best_here = (hit, origin)
        assert best_here is not None
        hit, origin = best_here
        per_pitch.append((
            hit / total_left, pitch, origin,
            _lattice_residual(left_pos, left_w, pitch, origin),
        ))

    # Every lattice finer than the real one also explains the real one's
    # edges, so raw explanatory power is maximised by the finest pitch
    # searched - a track almost everywhere, which explains everything and
    # means nothing. The count of columns is therefore charged for: a grid
    # has to earn each additional column by explaining materially more.
    #
    # This is the whole reason recovery is hard. Without the charge the
    # answer is always "23 columns, 86% explained", and every page measured
    # against it scores well while looking like nothing in particular.
    right_bound = max((e.position for e in rights), default=1.0)

    def _implied_columns(pitch_value: float, origin_value: float) -> int:
        span = max(0.0, right_bound - origin_value)
        return max(1, int(round(span / pitch_value)))

    scored = []
    for share, pitch, origin, residual in per_pitch:
        columns = _implied_columns(pitch, origin)
        if columns > MAX_COLUMNS:
            continue
        scored.append((share - COLUMN_CHARGE * columns, share, pitch, origin,
                       residual, columns))
    if not scored:
        return None
    _, share, pitch, origin, residual, _ = max(scored, key=lambda e: e[0])

    margin_left = origin
    while margin_left - pitch >= min(left_pos) - SNAP_TOLERANCE:
        margin_left -= pitch
    if margin_left < 0:
        margin_left += pitch

    gutter = _recover_gutter(rights, margin_left, pitch)
    column_width = max(0.005, pitch - gutter)

    span = max(0.0, right_bound - margin_left)
    columns = max(1, min(MAX_COLUMNS, int(round((span + gutter) / pitch))))
    margin_right = max(0.0, 1.0 - (margin_left + columns * pitch - gutter))

    grid = RecoveredGrid(
        columns=columns,
        margin_left=margin_left,
        margin_right=margin_right,
        column_width=column_width,
        gutter=gutter,
        edge_snap=0.0,
        residual=residual,
        pitch=pitch,
    )
    snap = _edge_snap(edges, grid)
    grid = RecoveredGrid(
        columns=grid.columns,
        margin_left=grid.margin_left,
        margin_right=grid.margin_right,
        column_width=grid.column_width,
        gutter=grid.gutter,
        edge_snap=snap,
        residual=residual,
        pitch=pitch,
    )
    return grid if grid.trustworthy else None


def _recover_gutter(rights: Sequence[Edge], margin_left: float,
                    pitch: float) -> float:
    """The gutter, read off where the right edges fall inside each track.

    A right edge sits at ``margin + i*pitch + column_width``, so the offset
    of each right edge within its own track is the column width, and the
    gutter is what is left of the pitch.
    """
    if not rights:
        return pitch * 0.15
    offsets = []
    for edge in rights:
        offset = (edge.position - margin_left) % pitch
        offsets.append((offset, edge.weight))
    offsets.sort()
    total = sum(w for _, w in offsets) or 1.0
    running = 0.0
    median = offsets[-1][0]
    for offset, weight in offsets:
        running += weight
        if running >= total / 2.0:
            median = offset
            break
    column_width = median if median > 0 else pitch * 0.85
    return max(0.0, min(pitch * 0.6, pitch - column_width))


def _edge_snap(edges: Sequence[Edge], grid: RecoveredGrid) -> float:
    """Share of edge mass sitting on a track edge."""
    total = sum(e.weight for e in edges) or 1.0
    on = 0.0
    for edge in edges:
        _, distance = grid.snap(edge.position)
        if distance <= SNAP_TOLERANCE:
            on += edge.weight
    return on / total


def edges_from_boxes(boxes: Iterable[tuple[float, float, float, float]],
                     page_width: float) -> list[Edge]:
    """Left and right edges of a page's elements, weighted by element area.

    Area weighting is what stops a page's grid being decided by its
    footnotes: a body column and a page number are one edge each, and only
    one of them is evidence about the grid.
    """
    out: list[Edge] = []
    for x0, y0, x1, y1 in boxes:
        width = max(0.0, x1 - x0)
        height = max(0.0, y1 - y0)
        weight = max(1e-6, width * height)
        out.append(Edge(position=x0 / page_width, weight=weight, side="left"))
        out.append(Edge(position=x1 / page_width, weight=weight, side="right"))
    return out
