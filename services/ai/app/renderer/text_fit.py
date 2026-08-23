"""
Text-fit estimation.

Estimates whether a block of text will overflow its placeholder box at its
declared font size, and if so computes how much to shrink it by - either as
a python-pptx font-size reduction (default/no-template render path, where
font sizes are already assigned at render time) or as an OOXML
<a:normAutofit fontScale/lnSpcReduction> pair (template-clone path, where
touching <a:rPr> directly would break template fidelity - normAutofit is
the same mechanism PowerPoint itself writes when text is typed past a
placeholder's bottom, and it shrinks at render time without altering the
run's own declared size).

This is a heuristic, not a font-metrics-accurate text layout engine - it
only needs to be conservative enough to catch real overflow before it
reaches the renderer. Stage G's validation step checks the actual rendered
output and is the final source of truth.
"""

from __future__ import annotations
import math
from dataclasses import dataclass

# Average glyph advance width as a fraction of font size, for a typical
# proportional sans-serif face (Arial/Calibri-class). Real glyph widths vary
# per character; this heuristic trades precision for being cheap to compute
# on every text box without a font-metrics library.
AVG_CHAR_WIDTH_RATIO = 0.52
# The face used when a caller does not name one. Sans, because that is what
# the overwhelming majority of decks are set in, and because a wrong-but-
# close family still measures far better than a fixed character ratio.
DEFAULT_MEASURE_FAMILY = "Arial"
# Text is never set to the full height of its box.
#
# Width is now measured exactly - the line breaks agree with LibreOffice on
# every case in the benchmark - but *height* is still lines times size times
# LINE_HEIGHT_RATIO, and 1.2 is OOXML's default rather than any particular
# font's real line height. So the horizontal reading is exact and the
# vertical one is a model, and a block allowed to fill 100% of its box is
# being placed on the strength of the half that is still approximate.
#
# This is a decision policy, not a correction to the measurement: no
# typesetter sets copy to the full depth of its column either.
FIT_HEADROOM = 0.94
LINE_HEIGHT_RATIO = 1.2  # default OOXML line spacing multiplier

# Box insets subtracted before fitting text, approximating OOXML's default
# <a:bodyPr> internal margins (lIns/rIns 0.1in, tIns/bIns 0.05in each).
DEFAULT_INSET_H_PT = 14.4
DEFAULT_INSET_V_PT = 7.2

MIN_FONT_SCALE = 0.4  # never shrink below 40% of the declared size here -
# beyond that, it's a genuine "too much content for this layout" problem for
# Stage G's validation/retry loop to catch, not something to silently force.
_FONT_SCALE_STEPS = (1.0, 0.92, 0.85, 0.77, 0.70, 0.62, 0.55, 0.47, MIN_FONT_SCALE)
# Matching OOXML lnSpcReduction values (thousandths of a percent).
_LN_SPC_REDUCTION_STEPS = (0, 0, 10000, 10000, 20000, 20000, 20000, 20000, 20000)


@dataclass
class TextBlock:
    text: str
    font_size_pt: float
    # The face this block is actually set in. Optional so every existing
    # caller keeps working, but worth passing: measuring Arial for a block
    # that renders in a wider face is how a block that "fits" runs off the
    # slide, and a fixed character ratio hid that by being wrong for
    # everything equally.
    family: str = ""
    # Bold is not a decoration for measurement: bold Liberation Sans is about
    # 5% wider than its regular, which is the difference between a headline
    # that sets on one line and one that sets on two.
    bold: bool = False


def estimate_wrapped_lines(text: str, font_size_pt: float, box_width_pt: float,
                           family: str = "", bold: bool = False) -> int:
    """How many wrapped lines `text` occupies at `font_size_pt` in a box that wide.

    Measured from the font's own advance widths rather than from a fixed
    average character width. Against 128 blocks rendered through the same
    LibreOffice path the previews use, the character-count estimate this
    replaced got 49.2% of line counts exactly right and was out by 0.70
    lines on average; reading the real metrics is exact on all 128.

    That error was not evenly spread, which is what made it damaging:
    "Illinois" and "WMWMWM" are the same number of characters and nearly
    twice apart in width, so narrow copy was reported as needing more room
    than it does and wide copy less. Everything built on the measurement -
    fitting, overflow detection, the density budget - inherited it.

    ``family`` is optional so every existing caller keeps working; without
    one the deck's default measuring face is used.
    """
    if not text or not text.strip():
        return 1
    if font_size_pt <= 0 or box_width_pt <= 0:
        return max(1, text.count("\n") + 1)

    try:
        from app.renderer.typeset.shape import wrap

        lines = wrap(text, box_width_pt, family or DEFAULT_MEASURE_FAMILY,
                     font_size_pt, bold=bold)
        return max(1, len(lines))
    except Exception:
        return _estimate_wrapped_lines_by_char_count(
            text, font_size_pt, box_width_pt
        )


def _estimate_wrapped_lines_by_char_count(text: str, font_size_pt: float,
                                          box_width_pt: float) -> int:
    """The previous estimator, kept as the fallback when no font resolves.

    A missing font should degrade the measurement, not fail the render.
    """
    char_width_pt = font_size_pt * AVG_CHAR_WIDTH_RATIO
    chars_per_line = max(1, int(box_width_pt / char_width_pt))

    lines = 0
    for para_line in text.split("\n"):
        words = para_line.split()
        if not words:
            lines += 1
            continue
        cur_len = 0
        line_count = 1
        for word in words:
            add_len = len(word) + 1
            if add_len > chars_per_line:
                if cur_len > 0:
                    line_count += 1
                line_count += math.ceil(add_len / chars_per_line) - 1
                cur_len = add_len % chars_per_line
                continue
            if cur_len + add_len > chars_per_line and cur_len > 0:
                line_count += 1
                cur_len = add_len
            else:
                cur_len += add_len
        lines += line_count
    return max(1, lines)


def estimate_blocks_height_pt(
    blocks: list[TextBlock],
    box_width_pt: float,
    line_spacing: float = LINE_HEIGHT_RATIO,
) -> float:
    """Total estimated wrapped height in points for a sequence of text blocks stacked vertically."""
    total = 0.0
    for block in blocks:
        lines = estimate_wrapped_lines(block.text, block.font_size_pt,
                                       box_width_pt, block.family, block.bold)
        total += lines * block.font_size_pt * line_spacing
    return total


def compute_autofit_scale(
    blocks: list[TextBlock],
    box_width_pt: float,
    box_height_pt: float,
    inset_h_pt: float = DEFAULT_INSET_H_PT,
    inset_v_pt: float = DEFAULT_INSET_V_PT,
) -> tuple[float, int]:
    """
    Returns (font_scale, ln_spc_reduction): font_scale is the 0-1 multiplier
    every block's declared font size should be shrunk by so the estimated
    wrapped height fits box_height_pt, and ln_spc_reduction is the matching
    OOXML <a:normAutofit lnSpcReduction> value. Returns (1.0, 0) if the text
    already fits at its declared size, or if there isn't enough geometry
    information to judge (zero-sized box).

    The insets default to OOXML's, which is right for a placeholder or a
    plain text box. A caller that zeroes its frame's margins - every mark the
    primitive layer draws does - must say so, or every box comes out 7.2pt
    short and its type shrinks by a step. That is not a rounding difference:
    it shrinks by a *different* step depending on how near the text sits to a
    line boundary, so a set of row bands rendered at four different sizes.
    """
    if box_height_pt <= 0 or box_width_pt <= 0 or not blocks:
        return 1.0, 0

    usable_width = max(1.0, box_width_pt - inset_h_pt)
    usable_height = max(1.0, box_height_pt - inset_v_pt)

    for scale, ln_reduction in zip(_FONT_SCALE_STEPS, _LN_SPC_REDUCTION_STEPS):
        effective_spacing = LINE_HEIGHT_RATIO * (1 - ln_reduction / 100000)
        # Carrying the face through matters: rebuilding the blocks without it
        # measured every candidate scale in the default face, so the whole
        # fitting decision was taken about a font the slide is not set in.
        scaled_blocks = [
            TextBlock(b.text, b.font_size_pt * scale, b.family, b.bold)
            for b in blocks
        ]
        height = estimate_blocks_height_pt(scaled_blocks, usable_width, effective_spacing)
        if height <= usable_height * FIT_HEADROOM:
            return scale, ln_reduction

    return MIN_FONT_SCALE, _LN_SPC_REDUCTION_STEPS[-1]
