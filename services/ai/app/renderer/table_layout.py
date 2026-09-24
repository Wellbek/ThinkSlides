"""
Turns a table's cell text into a layout: column widths derived from what is
actually in each column, per-column alignment, a font size that fits, and a
height the table genuinely needs.

Without this the renderer gave every column an equal share of the band and
every row an equal share of the height, whatever they held. A four-column
segment table rendered "Channel Development" and "1.8" in identical widths,
left-aligned its numbers like prose, and - because the requested row height
was larger than the text needed while LibreOffice still enforces its own
minimum - came out with visibly uneven rows and a dead band above and below.
"""

from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Optional

from app.renderer import text_fit

# A cell is numeric if it is a number possibly wrapped in the usual
# accounting decoration: currency, sign, thousands separators, percent,
# parenthesised negatives, or a bare dash standing in for "no value".
_NUMERIC_RE = re.compile(
    r"""^\(?           # optional opening paren for a negative
        [-+]?
        [$€£¥]?\s?
        \d{1,3}(?:[,\s]\d{3})*(?:\.\d+)?
        \s?[%xX]?
        \s?[kKmMbB]?
        \)?$""",
    re.VERBOSE,
)
_PLACEHOLDER_CELL = frozenset({"", "-", "–", "—", "n/a", "na", "tbd"})

# Rows whose first cell reads like a summary get the total treatment.
_TOTAL_LABELS = ("total", "sum", "overall", "combined", "all", "net", "grand total")

# Point sizes tried in order until the table fits its band.
_SIZE_STEPS = (14.0, 13.0, 12.0, 11.0, 10.0, 9.0, 8.0)
MIN_CELL_PT = _SIZE_STEPS[-1]

# Vertical padding inside a cell, as a multiple of the font size. OOXML's
# default cell insets are 0.05in top/bottom; this is the same order.
_CELL_PAD_RATIO = 0.85
# Horizontal padding, in points, per cell (both insets combined).
CELL_PAD_H_PT = 14.4
# No column may collapse below this, whatever its content measures.
_MIN_COL_FRACTION = 0.08


def is_numeric_cell(text: str) -> bool:
    """True when a cell holds a figure rather than prose."""
    value = (text or "").strip()
    if value.lower() in _PLACEHOLDER_CELL:
        return False
    return bool(_NUMERIC_RE.match(value))


@dataclass(frozen=True)
class TableLayout:
    """The resolved geometry and formatting for one table."""
    col_fractions: tuple[float, ...]
    numeric_columns: frozenset[int]
    header_pt: float
    body_pt: float
    row_heights_in: tuple[float, ...]
    total_row_index: int | None

    @property
    def height_in(self) -> float:
        return sum(self.row_heights_in)


def _column_is_numeric(rows: list[list[str]], col: int, has_header: bool) -> bool:
    """A column of figures, ignoring blanks and "no value" placeholders.

    The placeholders have to be skipped rather than counted against the
    column: a single "-" standing in for a missing store count was enough to
    make an otherwise all-numeric column render left-aligned like prose.
    """
    body = rows[1:] if has_header else rows
    values = [
        (row[col] or "").strip()
        for row in body
        if col < len(row) and (row[col] or "").strip()
    ]
    values = [v for v in values if v.lower() not in _PLACEHOLDER_CELL]
    if not values:
        return False
    return all(is_numeric_cell(v) for v in values)


def _find_total_row(rows: list[list[str]], has_header: bool) -> int | None:
    """The index of a summary row, if the table ends in one."""
    if len(rows) < (3 if has_header else 2):
        return None
    last = len(rows) - 1
    first_cell = (rows[last][0] if rows[last] else "").strip().lower()
    return last if first_cell in _TOTAL_LABELS else None


def _natural_widths(rows: list[list[str]], n_cols: int, font_pt: float) -> list[float]:
    """Each column's unwrapped text width in points."""
    widths = []
    for col in range(n_cols):
        longest = 0.0
        for row in rows:
            text = row[col] if col < len(row) else ""
            longest = max(longest, len(str(text)) * font_pt * text_fit.AVG_CHAR_WIDTH_RATIO)
        widths.append(longest + CELL_PAD_H_PT)
    return widths


def _word_width_pt(word: str, font_pt: float, bold: bool,
                   family: str = text_fit.DEFAULT_MEASURE_FAMILY) -> float:
    """A word's advance width, in the face the table is drawn in."""
    from app.renderer.typeset.fonts import width_pt

    try:
        return width_pt(word, family, font_pt, bold=bold)
    except Exception:
        return len(word) * font_pt * text_fit.AVG_CHAR_WIDTH_RATIO


def _word_floors(rows: list[list[str]], n_cols: int, font_pt: float,
                 has_header: bool, total_row: Optional[int],
                 family: str) -> list[float]:
    """Each column's narrowest width in points: its longest word, unbroken.

    Proportional widths come from a column's longest *cell*, so a column of
    one-word labels beside a column of sentences was squeezed below the width
    of its own words, and "Strategy" was set as "Stra / tegy".
    """
    floors = []
    for col in range(n_cols):
        longest = 0.0
        for index, row in enumerate(rows):
            text = str(row[col]) if col < len(row) else ""
            # A header is set a point larger, and bold; a total row bold.
            header = has_header and index == 0
            size = font_pt + 1.0 if header else font_pt
            bold = header or index == total_row
            for word in text.split():
                longest = max(longest, _word_width_pt(word, size, bold, family))
        floors.append(longest + CELL_PAD_H_PT)
    return floors


def _fit_floors(fractions: tuple[float, ...], floors_pt: list[float],
                width_pt: float) -> Optional[tuple[float, ...]]:
    """These fractions, raised so no column is narrower than its floor.

    None when the floors alone are wider than the table, which is the
    caller's cue to try a smaller size.
    """
    if width_pt <= 0:
        return None
    floors = [f / width_pt for f in floors_pt]
    if sum(floors) > 1.0:
        return None
    lifted = [max(f, floor) for f, floor in zip(fractions, floors)]
    excess = sum(lifted) - 1.0
    if excess > 0:
        headroom = [max(0.0, f - floor) for f, floor in zip(lifted, floors)]
        pool = sum(headroom) or 1.0
        lifted = [f - excess * (h / pool) for f, h in zip(lifted, headroom)]
    return tuple(lifted)


def _fractions(widths: list[float]) -> tuple[float, ...]:
    """Normalise natural widths into fractions, with a floor per column.

    The floor matters for a table mixing "Channel Development" with "1.8":
    proportional-only allocation squeezes the numeric column to a sliver
    that then wraps its own header.
    """
    total = sum(widths) or 1.0
    raw = [w / total for w in widths]

    floor = min(_MIN_COL_FRACTION, 1.0 / len(raw))
    lifted = [max(f, floor) for f in raw]
    excess = sum(lifted) - 1.0
    if excess > 0:
        # Take the overshoot back off the columns that have room to give.
        headroom = [max(0.0, f - floor) for f in lifted]
        pool = sum(headroom) or 1.0
        lifted = [f - excess * (h / pool) for f, h in zip(lifted, headroom)]
    return tuple(lifted)


def _row_height_in(row: list[str], col_widths_pt: list[float], font_pt: float,
                   bold: bool = False,
                   family: str = text_fit.DEFAULT_MEASURE_FAMILY) -> float:
    """How tall a row has to be for its tallest wrapped cell, measured in the
    face and weight it is drawn in: a bold header measured regular wrapped
    onto a line more than its row had room for."""
    lines = 1
    for col, width_pt in enumerate(col_widths_pt):
        text = str(row[col]) if col < len(row) else ""
        usable = max(1.0, width_pt - CELL_PAD_H_PT)
        lines = max(lines, text_fit.estimate_wrapped_lines(
            text, font_pt, usable, family=family, bold=bold))
    height_pt = lines * font_pt * text_fit.LINE_HEIGHT_RATIO + font_pt * _CELL_PAD_RATIO
    return height_pt / 72.0


# The smallest body size a table on a slide is read at. Below it the planner
# will still fit a table into a band, but the band was too small.
LEGIBLE_CELL_PT = 10.0


def needed_height_in(rows: list[list[str]], has_header: bool,
                     width_in: float, body_pt: float = LEGIBLE_CELL_PT,
                     family: str = text_fit.DEFAULT_MEASURE_FAMILY) -> float:
    """How tall this table is at this width and size, with nothing compressed.

    What a layout has to offer a table for it to be read: `plan_table` fits
    any table into any band by shrinking the type and then the rows, and a
    band too short for the table's wrapped cells came out with its rows
    printed over each other.
    """
    layout = plan_table(rows, has_header, width_in, float("inf"),
                        sizes=(body_pt,), family=family)
    return layout.height_in if layout else 0.0


def plan_table(
    rows: list[list[str]],
    has_header: bool,
    region_width_in: float,
    region_height_in: float,
    sizes: tuple[float, ...] = _SIZE_STEPS,
    family: str = text_fit.DEFAULT_MEASURE_FAMILY,
) -> TableLayout | None:
    """
    Resolve a table's column widths, alignment, type size and row heights.

    ``family`` is the face the table will be drawn in. Measuring in one face
    and drawing in another is how planned rows came out too short for the
    lines drawn in them.

    Returns None for an empty table. The returned height may be less than
    ``region_height_in`` - a table that needs four rows should occupy four
    rows' worth of the slide and let the caller centre it, not be stretched
    until its rows are inches tall.
    """
    if not rows:
        return None
    n_cols = max((len(row) for row in rows), default=0)
    if n_cols == 0:
        return None

    normalised = [[str(row[c]) if c < len(row) else "" for c in range(n_cols)] for row in rows]

    numeric_columns = frozenset(
        col for col in range(n_cols) if _column_is_numeric(normalised, col, has_header)
    )
    total_row_index = _find_total_row(normalised, has_header)

    region_width_pt = region_width_in * 72.0
    chosen = None
    for body_pt in sizes:
        header_pt = body_pt + 1.0
        fractions = _fractions(_natural_widths(normalised, n_cols, body_pt))
        floored = _fit_floors(
            fractions,
            _word_floors(normalised, n_cols, body_pt, has_header,
                         total_row_index, family),
            region_width_pt)
        if floored is None and body_pt != sizes[-1]:
            continue          # some word cannot be set whole at this size
        fractions = floored or fractions
        col_widths_pt = [f * region_width_pt for f in fractions]
        heights = [
            _row_height_in(
                row, col_widths_pt,
                header_pt if (has_header and i == 0) else body_pt,
                bold=(has_header and i == 0) or i == total_row_index,
                family=family)
            for i, row in enumerate(normalised)
        ]
        chosen = (fractions, header_pt, body_pt, heights)
        if sum(heights) <= region_height_in:
            break

    fractions, header_pt, body_pt, heights = chosen

    # Still too tall at the smallest legible size: compress proportionally
    # rather than let the table run off the slide. Stage G's validation on
    # the real rendered file is what catches a table that genuinely cannot
    # fit at all.
    overflow = sum(heights)
    if overflow > region_height_in and overflow > 0:
        scale = region_height_in / overflow
        heights = [h * scale for h in heights]

    return TableLayout(
        col_fractions=fractions,
        numeric_columns=numeric_columns,
        header_pt=header_pt,
        body_pt=body_pt,
        row_heights_in=tuple(heights),
        total_row_index=total_row_index,
    )
