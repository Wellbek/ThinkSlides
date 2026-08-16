"""
Vertical balance for a slide's body text.

Every content slide was top-anchored at a fixed type size in a fixed band,
so a slide with three bullets left the bottom 40-60% of the page blank while
a slide with eight filled it. The deck read as a set of half-finished pages.

Real decks solve this the way print does: a short block is set larger and
leaded more generously, and the whole block is placed optically rather than
flushed to the top of its box. This module works out those three numbers -
a size multiplier, the gap between paragraphs, and how far down the band the
block should start - from the text that is actually there.

Deliberately not "stretch to fill": text spread edge-to-edge down a slide
reads as broken, not balanced. Each adjustment is bounded, and a block that
already fills its band comes back untouched.

It works in both directions. The module was written for the sparse case and
only ever grew, because that was the only failure a four-bullet deck could
have. Once slides are written to a density budget (#129) the other case
appears: a block that overruns its band. Left to itself the caller's autofit
takes it in one step to whatever fits, which is how one slide ends up two
sizes off the rest of the deck. So a small, bounded tighten happens here
first, on the same reasoning that bounds the growth - the deck should look
like one type scale, not one per slide - and autofit remains the backstop for
anything this cannot rescue.

What this still refuses to do is *add* content. A slide with four bullets on a
page that wants ten is not fixed by setting the four larger; that is the same
sparse page in bigger type. The budget rejects it upstream, where more content
can actually come from.
"""

from __future__ import annotations
from dataclasses import dataclass

from app.renderer import text_fit

# Above this fill ratio the block is doing its job and is left alone.
COMFORTABLE_FILL = 0.72
# ...until it passes this, at which point it is overrunning its band and the
# tightening below applies. The gap between the two is the band where a block
# is simply full, and touching it either way would be fussing.
CROWDED_FILL = 1.0
# A block may be set smaller, but only this far - the mirror of MAX_GROWTH and
# for the same reason. Past it the slide stops matching the deck's type scale,
# and the honest answer is that the slide has too much on it.
MAX_TIGHTEN = 0.88
# Where a tightened block should land. Slightly under the box rather than
# exactly at it, because the estimate is an estimate.
TIGHTENED_FILL = 0.96
# A short block may be set larger, but only so far. Two limits bite here:
# past about this the slide reads as shouting rather than well-set, and body
# copy that varies wildly slide to slide is its own design smell - the deck
# should look like one type scale, not one per slide.
MAX_GROWTH = 1.15
# Extra leading between top-level points, in points. Roughly one blank line
# at body size; beyond that the points stop reading as one list.
MAX_PARAGRAPH_GAP_PT = 22.0
# Fraction of the leftover room placed above the block. A third above and
# two thirds below reads as hanging from the title, which is what a reader
# expects; a true half-and-half centre looks like it has floated loose.
TOP_SHARE = 1.0 / 3.0


@dataclass(frozen=True)
class BodyBalance:
    """How to set a body block so it sits well in its band."""
    size_scale: float
    paragraph_gap_pt: float
    top_offset_in: float

    @property
    def is_noop(self) -> bool:
        return (self.size_scale == 1.0
                and self.paragraph_gap_pt == 0.0
                and self.top_offset_in == 0.0)


NO_CHANGE = BodyBalance(size_scale=1.0, paragraph_gap_pt=0.0, top_offset_in=0.0)


def _measure_in(texts: list[str], sizes: list[float], usable_width_pt: float,
                scale: float = 1.0) -> float:
    blocks = [
        text_fit.TextBlock(text=text, font_size_pt=size * scale)
        for text, size in zip(texts, sizes)
    ]
    return text_fit.estimate_blocks_height_pt(blocks, usable_width_pt) / 72.0


def balance_body(
    texts: list[str],
    sizes: list[float],
    box_width_in: float,
    box_height_in: float,
) -> BodyBalance:
    """
    Work out the size, leading and offset for one body block.

    ``sizes`` are the point sizes the type scale already resolved for each
    run. The result is advisory - the caller still applies its own autofit,
    so nothing here can push text out of its box.
    """
    if not texts or box_width_in <= 0 or box_height_in <= 0:
        return NO_CHANGE

    usable_width_pt = max(1.0, box_width_in * 72.0 - text_fit.DEFAULT_INSET_H_PT)
    measured = _measure_in(texts, sizes, usable_width_pt)
    if measured <= 0:
        return NO_CHANGE

    fill = measured / box_height_in
    if fill > CROWDED_FILL:
        return _tighten(texts, sizes, usable_width_pt, box_height_in)
    if fill >= COMFORTABLE_FILL:
        return NO_CHANGE

    # 1. Grow the type, but never past the point where the block would
    #    overshoot a comfortable fill on its own.
    scale = 1.0
    if fill < COMFORTABLE_FILL:
        wanted = min(MAX_GROWTH, COMFORTABLE_FILL / fill)
        # Re-measure rather than assuming height scales linearly: growing the
        # type also rewraps, which can add a line and eat the gain.
        for candidate in _steps(wanted):
            if _measure_in(texts, sizes, usable_width_pt, candidate) <= (
                box_height_in * COMFORTABLE_FILL
            ):
                scale = candidate
                break

    measured = _measure_in(texts, sizes, usable_width_pt, scale)
    surplus_in = max(0.0, box_height_in - measured)

    # 2. Lead the paragraphs apart with part of what is left.
    gaps = max(0, len(texts) - 1)
    gap_pt = 0.0
    if gaps and surplus_in > 0:
        gap_pt = min(MAX_PARAGRAPH_GAP_PT, surplus_in * 0.55 * 72.0 / gaps)
        surplus_in -= gap_pt * gaps / 72.0

    # 3. Place what remains, mostly below the block.
    return BodyBalance(
        size_scale=scale,
        paragraph_gap_pt=gap_pt,
        top_offset_in=max(0.0, surplus_in) * TOP_SHARE,
    )


def _tighten(texts: list[str], sizes: list[float], usable_width_pt: float,
             box_height_in: float) -> BodyBalance:
    """
    Set an overfull block a little smaller, within the deck's type scale.

    The paragraph gap and the offset stay at zero: there is nothing left over
    to lead with, and a block that overruns its band should sit at the top of
    it. Only the size moves, and only as far as MAX_TIGHTEN - a block this
    cannot rescue is handed on to the caller's autofit unchanged, which is the
    honest outcome for a slide carrying more than its band holds.
    """
    target_in = box_height_in * TIGHTENED_FILL
    for candidate in _tighten_steps():
        if _measure_in(texts, sizes, usable_width_pt, candidate) <= target_in:
            return BodyBalance(size_scale=candidate, paragraph_gap_pt=0.0,
                               top_offset_in=0.0)
    return BodyBalance(size_scale=MAX_TIGHTEN, paragraph_gap_pt=0.0,
                       top_offset_in=0.0)


def _steps(upper: float) -> list[float]:
    """Growth factors to try, largest first, never below 1.0."""
    candidates = [upper, 1.2, 1.15, 1.1, 1.05, 1.0]
    return [round(c, 3) for c in candidates if c <= upper + 1e-9] or [1.0]


def _tighten_steps() -> list[float]:
    """Shrink factors to try, gentlest first, never below MAX_TIGHTEN."""
    return [round(c, 3) for c in (0.97, 0.94, 0.91, MAX_TIGHTEN)]
