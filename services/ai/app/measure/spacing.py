"""
Recovering the ladder of gaps a publication spaces things by.

A designed page does not use arbitrary distances. It uses a small set - a
base unit and a handful of multiples - and reuses them, which is most of why
the page reads as one system rather than as a set of independently placed
elements. Recovering that ladder is what lets a generated page say "a step
three gap here" instead of inventing 0.37 inches.

Same discipline as the grid and the rhythm: report how much of the observed
spacing the ladder actually explains, and refuse when it explains too
little. A page spaced by hand has no ladder, and pretending otherwise gives
every later stage a fiction to converge on.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

MIN_BASE_PT = 2.0
MAX_BASE_PT = 24.0
BASE_STEP_PT = 0.1
# A gap within this many points of a rung counts as sitting on it.
SNAP_TOLERANCE_PT = 1.2
MIN_EXPLAINED = 0.6
# The multiples a ladder is allowed to use. Deliberately short: a ladder with
# a rung at every integer explains everything and constrains nothing.
LADDER = (0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0)


@dataclass(frozen=True)
class RecoveredSpacing:
    """A base unit, and how much of the page's spacing it accounts for."""

    base_pt: float
    steps: tuple[float, ...]
    explained: float
    samples: int

    @property
    def trustworthy(self) -> bool:
        return self.explained >= MIN_EXPLAINED

    def nearest_step(self, gap_pt: float) -> int:
        """The rung this gap is closest to."""
        best, best_distance = 0, float("inf")
        for index, multiple in enumerate(self.steps):
            distance = abs(gap_pt - self.base_pt * multiple)
            if distance < best_distance:
                best, best_distance = index, distance
        return best


def gaps_from_page(page, min_gap_pt: float = 2.0,
                   max_gap_pt: float = 220.0) -> list[float]:
    """Vertical gaps between the page's text blocks, top to bottom.

    Measured between blocks rather than between lines: the distance between
    two lines of a paragraph is the leading, which the rhythm already
    describes, and mixing the two reads a page's line spacing as its spacing
    ladder.
    """
    boxes = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        text = "".join(
            span["text"] for line in block.get("lines", []) for span in line["spans"]
        ).strip()
        if text:
            boxes.append(block["bbox"])

    # Group into columns by horizontal overlap, then take only *consecutive*
    # blocks down each column. Every pair of blocks that happen to share an x
    # range is not a spacing decision - the distance from the first paragraph
    # to the fifth is an accumulation, not a gap anyone chose, and pooling
    # those swamps the handful of real decisions with arithmetic.
    columns: list[list[tuple]] = []
    for box in sorted(boxes, key=lambda b: (b[0], b[1])):
        for column in columns:
            reference = column[0]
            overlap = min(reference[2], box[2]) - max(reference[0], box[0])
            width = min(reference[2] - reference[0], box[2] - box[0])
            if width > 0 and overlap / width >= 0.6:
                column.append(box)
                break
        else:
            columns.append([box])

    gaps: list[float] = []
    for column in columns:
        ordered = sorted(column, key=lambda b: b[1])
        for first, second in zip(ordered, ordered[1:]):
            gap = second[1] - first[3]
            if min_gap_pt <= gap <= max_gap_pt:
                gaps.append(round(gap, 1))
    return gaps


def recover_spacing(gaps: Sequence[float]) -> Optional[RecoveredSpacing]:
    """The ladder these gaps sit on, or None when they sit on none."""
    usable = [g for g in gaps if g > 0]
    if len(usable) < 6:
        return None

    best = None
    base = MIN_BASE_PT
    while base <= MAX_BASE_PT:
        explained = 0
        for gap in usable:
            for multiple in LADDER:
                if abs(gap - base * multiple) <= SNAP_TOLERANCE_PT:
                    explained += 1
                    break
        share = explained / len(usable)
        # A tiny base makes every rung dense enough to catch any gap, so it
        # is charged for exactly as extra columns are in the grid.
        score = share - 0.02 * (MIN_BASE_PT / base)
        if best is None or score > best[0]:
            best = (score, base, share)
        base = round(base + BASE_STEP_PT, 3)

    assert best is not None
    _, base, share = best
    recovered = RecoveredSpacing(base_pt=base, steps=LADDER, explained=share,
                                 samples=len(usable))
    return recovered if recovered.trustworthy else None
