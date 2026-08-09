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
from dataclasses import dataclass

# Average glyph advance width as a fraction of font size, for a typical
# proportional sans-serif face (Arial/Calibri-class). Real glyph widths vary
# per character; this heuristic trades precision for being cheap to compute
# on every text box without a font-metrics library.
AVG_CHAR_WIDTH_RATIO = 0.52
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


def estimate_wrapped_lines(text: str, font_size_pt: float, box_width_pt: float) -> int:
    """Estimate how many wrapped lines `text` occupies at `font_size_pt` in a box `box_width_pt` wide."""
    if not text or not text.strip():
        return 1
    if font_size_pt <= 0 or box_width_pt <= 0:
        return max(1, text.count("\n") + 1)

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
        lines = estimate_wrapped_lines(block.text, block.font_size_pt, box_width_pt)
        total += lines * block.font_size_pt * line_spacing
    return total


def compute_autofit_scale(
    blocks: list[TextBlock],
    box_width_pt: float,
    box_height_pt: float,
) -> tuple[float, int]:
    """
    Returns (font_scale, ln_spc_reduction): font_scale is the 0-1 multiplier
    every block's declared font size should be shrunk by so the estimated
    wrapped height fits box_height_pt, and ln_spc_reduction is the matching
    OOXML <a:normAutofit lnSpcReduction> value. Returns (1.0, 0) if the text
    already fits at its declared size, or if there isn't enough geometry
    information to judge (zero-sized box).
    """
    if box_height_pt <= 0 or box_width_pt <= 0 or not blocks:
        return 1.0, 0

    usable_width = max(1.0, box_width_pt - DEFAULT_INSET_H_PT)
    usable_height = max(1.0, box_height_pt - DEFAULT_INSET_V_PT)

    for scale, ln_reduction in zip(_FONT_SCALE_STEPS, _LN_SPC_REDUCTION_STEPS):
        effective_spacing = LINE_HEIGHT_RATIO * (1 - ln_reduction / 100000)
        scaled_blocks = [TextBlock(b.text, b.font_size_pt * scale) for b in blocks]
        height = estimate_blocks_height_pt(scaled_blocks, usable_width, effective_spacing)
        if height <= usable_height:
            return scale, ln_reduction

    return MIN_FONT_SCALE, _LN_SPC_REDUCTION_STEPS[-1]
