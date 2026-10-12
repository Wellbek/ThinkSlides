"""
Breaking text into lines the way the renderer will.

Greedy first-fit on word boundaries, which is what PowerPoint and
LibreOffice both do - not the Knuth-Plass optimal break, which produces
better-looking paragraphs and would therefore disagree with what actually
gets drawn. The purpose here is to predict the renderer, not to improve on
it: a measurement that is prettier than the truth is still wrong.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence

from app.renderer.typeset.fonts import FontMetrics, metrics_for

# Break opportunities: whitespace, and after a hyphen or an em/en dash, which
# is where both renderers will break a long compound.
_BREAK = re.compile(r"(\s+|(?<=[-–—]))")


@dataclass(frozen=True)
class LineBox:
    """One laid-out line."""

    text: str
    width_pt: float
    top_pt: float
    baseline_pt: float


@dataclass(frozen=True)
class TextLayout:
    """The result of setting a block of text in a box."""

    lines: tuple[LineBox, ...]
    height_pt: float
    overflow_pt: float
    truncated: bool = False

    @property
    def line_count(self) -> int:
        return len(self.lines)

    @property
    def fits(self) -> bool:
        return self.overflow_pt <= 0.0


def _tokens(text: str) -> list[str]:
    parts = [p for p in _BREAK.split(text) if p]
    tokens: list[str] = []
    for part in parts:
        if part.isspace():
            if tokens:
                tokens[-1] = tokens[-1] + " "
            continue
        tokens.append(part)
    return tokens


def _break_word(token: str, width_pt: float, measure) -> tuple[list[str], str]:
    """Split an over-wide word, returning the full lines and the remainder."""
    lines: list[str] = []
    current = ""
    for char in token:
        if current and measure(current + char) > width_pt:
            lines.append(current)
            current = char
        else:
            current += char
    return lines, current


def wrap(text: str, width_pt: float, family: str, size_pt: float, *,
         bold: bool = False, italic: bool = False,
         tracking_per_mille: int = 0,
         metrics: Optional[FontMetrics] = None,
         limit: Optional[int] = None) -> list[str]:
    """The lines this text breaks into inside a box of this width.

    ``limit`` stops setting once more than that many lines are set, for a
    caller that only needs to know what the first few lines hold.
    """
    if width_pt <= 0 or not text.strip():
        return [text] if text.strip() else []
    if metrics is None:
        metrics = metrics_for(family, bold=bold, italic=italic)

    def measure(value: str) -> float:
        if metrics is None:
            return len(value) * size_pt * 0.52
        return metrics.width_pt(value, size_pt, tracking_per_mille)

    lines: list[str] = []
    for paragraph in text.split("\n"):
        if limit is not None and len(lines) > limit:
            break
        if not paragraph.strip():
            lines.append("")
            continue
        current = ""
        # The width of `current`, kept as it grows. A string's width is the
        # sum of its characters' (advances plus tracking, no kerning), so
        # adding each word's is exact - and re-measuring the whole line for
        # every word made setting a long column quadratic in its width.
        current_w = 0.0
        for token in _tokens(paragraph):
            if limit is not None and len(lines) > limit:
                break
            word = token.rstrip()
            word_w = measure(word)
            # Trailing space does not count against the line: a word that
            # ends exactly at the margin still fits, and counting its space
            # wraps a line early - which is how a measurement drifts one
            # line long on every paragraph.
            if current_w + word_w <= width_pt:
                current += token
                current_w += word_w + measure(token[len(word):])
                continue
            if current:
                lines.append(current.rstrip())
                current = ""
            # A single word wider than its column is broken inside the word,
            # which is what both renderers do. Without this, "unprecedented"
            # set at 24pt in a 115pt column is reported as one line and
            # drawn as three.
            if word_w > width_pt:
                head, token = _break_word(token, width_pt, measure)
                lines.extend(head)
            current = token
            current_w = measure(token)
        if current.strip():
            lines.append(current.rstrip())
    return lines


def layout(text: str, width_pt: float, height_pt: float, family: str,
           size_pt: float, *, line_height_pt: float = 0.0, bold: bool = False,
           italic: bool = False, tracking_per_mille: int = 0,
           first_baseline_pt: Optional[float] = None,
           max_lines: Optional[int] = None,
           columns: int = 1, column_gap_pt: float = 12.0) -> TextLayout:
    """Set this text in this box, and say by how much it does not fit.

    Overflow is *reported*, never absorbed. The previous renderer's answer to
    a block that did not fit was to shrink the type until it did, which
    silently breaks the type scale the whole deck is set on - and it did so
    invisibly, so a page that was over-filled looked like a page that was
    correctly filled in slightly smaller type.
    """
    # Setting in columns narrows the measure and multiplies the depth: the
    # same copy in two columns wraps at half the width and needs half the
    # height. Measuring it at full width reports a block that fits and draws
    # one that overruns by a factor of two.
    columns = max(1, int(columns))
    if columns > 1:
        width_pt = max(1.0, (width_pt - column_gap_pt * (columns - 1)) / columns)

    metrics = metrics_for(family, bold=bold, italic=italic)
    if line_height_pt <= 0:
        line_height_pt = (
            metrics.natural_line_height_pt(size_pt) if metrics
            else size_pt * 1.2
        )
    ascent = (
        metrics.ascent * size_pt / metrics.units_per_em if metrics
        else size_pt * 0.8
    )
    first = first_baseline_pt if first_baseline_pt is not None else ascent

    raw = wrap(text, width_pt, family, size_pt, bold=bold, italic=italic,
               tracking_per_mille=tracking_per_mille, metrics=metrics)
    truncated = False
    if max_lines is not None and len(raw) > max_lines:
        raw = raw[:max_lines]
        truncated = True

    boxes = []
    for index, line in enumerate(raw):
        baseline = first + index * line_height_pt
        width = (
            metrics.width_pt(line, size_pt, tracking_per_mille) if metrics
            else len(line) * size_pt * 0.52
        )
        boxes.append(LineBox(text=line, width_pt=width,
                             top_pt=baseline - ascent, baseline_pt=baseline))

    lines_per_column = -(-len(raw) // columns) if raw else 0
    used = ((first - ascent) + lines_per_column * line_height_pt) if raw else 0.0
    return TextLayout(
        lines=tuple(boxes),
        height_pt=used,
        overflow_pt=max(0.0, used - height_pt) if height_pt > 0 else 0.0,
        truncated=truncated,
    )


def fits_at(text: str, width_pt: float, height_pt: float, family: str,
            size_pt: float, **kwargs) -> bool:
    return layout(text, width_pt, height_pt, family, size_pt, **kwargs).fits


def largest_size_that_fits(text: str, width_pt: float, height_pt: float,
                           family: str, sizes: Sequence[float],
                           **kwargs) -> Optional[float]:
    """The biggest size from a role's permitted range that still fits.

    Sizes come from the pack's ramp rather than from a continuous search, so
    a fitted block still sits on the deck's type scale instead of acquiring
    a size of its own.
    """
    for size in sorted(sizes, reverse=True):
        if fits_at(text, width_pt, height_pt, family, size, **kwargs):
            return size
    return None
