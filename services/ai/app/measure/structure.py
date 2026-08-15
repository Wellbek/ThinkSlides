"""
Counting the blocks on a page the way a reader would.

``page_metrics.block_count`` is a density *proxy*: a clustering of content at a
fixed threshold, good for "is this page denser than that one" and deliberately
not claiming to be a human's count. That is the right measure for scoring, and
the wrong one for learning a template's density budget - a budget of "17.4
clusters per slide" cannot be handed to a planner.

So this counts blocks the way the reference deck's own description of itself
does:

* page 40 is **seven row bands**, not fourteen cells - a narrow label column
  beside a wide prose column is one band
* page 12 is **fifteen sentence-cells** - three columns of similar width is a
  matrix, and each cell is its own block
* page 95 is **four cards and a banner** - a card's icon, heading and two
  paragraphs are one block, stacked in one column

Those three are consistent under one rule, which is what makes it a rule rather
than three special cases: **a block is a cell of the page's grid, minus the
grid's own headers.** A cell may contain several paragraphs; a column header
and a row label are furniture, not content.

Two distinctions do the work, and both are about telling apart arrangements
that are geometrically identical:

* A **column header** and the first row of a grid look the same. What
  separates them is that a header is set larger *and* has more than one row
  under it - a card's heading over its body is the same shape with one row
  under it, and that is a card, not a header.
* A **row label** and a narrow first column look the same. What separates them
  is that a label is short and much narrower than the columns it labels.

The page's title and the lead paragraph above the grid are excluded for the
same reason: they are on every page of a well-made deck, so counting them
would report every page as carrying three more blocks than it does.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from app.measure.page_metrics import (
    APPARATUS_MAX_PT, CONTENT_INSET_BOTTOM, CONTENT_INSET_TOP, CONTENT_INSET_X,
    _collect_drawings, _collect_images, _collect_spans, _find_charts,
    _intersects, _union,
)

# Lines this close vertically are the same paragraph, and this close
# horizontally are the same column. Measured off the reference: page 12's
# wrapped lines sit ~1pt apart and its table rows ~8pt apart, and its columns
# are separated by a 5pt gutter - so both thresholds have to be tight.
CELL_MERGE_Y_PT = 5.0
CELL_MERGE_X_PT = 3.5
# A heading sits further above its body than that, so a *size change* down a
# column is what joins them: page 95's card heading is 16pt over 12pt body
# with a 10pt gap, and the two are one card.
HEADING_MERGE_Y_PT = 13.0
HEADING_SIZE_STEP_PT = 2.0
HEADING_OVERLAP = 0.6

# Two cells in a row are one band when the wider is this much wider - a label
# column beside a prose column. Three or more cells is a grid whatever their
# widths.
BAND_MAX_CELLS = 2
BAND_MIN_WIDTH_RATIO = 2.2
BAND_MIN_SPAN = 0.55

# A single cell spanning this much of the content width is a full-width line.
FULL_WIDTH_SPAN = 0.78
# A header row is set larger than the rows it heads by at least this much...
HEADER_SIZE_STEP_PT = 1.5
# ...and has at least this many rows under it. One row under a larger line is
# a card heading over its body, not a column header.
HEADER_MIN_ROWS_BELOW = 2
# How far outside its own plot area an exhibit's labels may sit and still
# belong to it.
EXHIBIT_LABEL_PAD = 0.035

# A row label is this much narrower than the columns it labels. Width is the
# whole test: a first column of prose is as wide as the ones beside it, and a
# label is not.
LABEL_COLUMN_WIDTH_RATIO = 1.8


@dataclass(frozen=True)
class Cell:
    """One cell of a page's grid, with the type size it is set in."""
    box: tuple[float, float, float, float]
    size_pt: float

    @property
    def width(self) -> float:
        return self.box[2] - self.box[0]


@dataclass(frozen=True)
class PageStructure:
    """A page's blocks, and the furniture that was not counted as one."""
    block_count: int
    rows: int
    columns: int
    band_rows: int
    excluded: tuple[str, ...] = ()
    # Charts and figures found on the page. Counted here because the caller
    # would otherwise re-run the exhibit finder to learn it, and this already
    # had to find them to keep their labels from being counted as blocks.
    exhibits: int = 0


def count_blocks(page) -> PageStructure:
    """Count the blocks on a ``fitz.Page`` the way its designer would."""
    width, height = float(page.rect.width), float(page.rect.height)
    content = (
        width * CONTENT_INSET_X,
        height * CONTENT_INSET_TOP,
        width * (1.0 - CONTENT_INSET_X),
        height * (1.0 - CONTENT_INSET_BOTTOM),
    )
    # An exhibit is one block, and its axis and category labels belong to it
    # rather than to the page - counting them turned a chart with seven
    # category rows into seven blocks.
    exhibits = _find_charts(page, _collect_drawings(page, width, height),
                            _collect_images(page, width, height), width, height)
    # Padded, because a chart's category labels sit just *outside* its plot
    # area - reference page 22's seven category rows are 9pt to the left of
    # the bars, and counting them turned one chart into eight blocks.
    pad = width * EXHIBIT_LABEL_PAD
    lines = [line for line in _body_lines(page, content)
             if not any(_intersects(line.box, e.bbox, pad, pad) for e in exhibits)]
    if not lines and not exhibits:
        return PageStructure(block_count=0, rows=0, columns=0, band_rows=0,
                             exhibits=0)

    excluded: list[str] = []
    lines, largest = _drop_title(lines, excluded)
    lines = _drop_column_header(lines, excluded)
    cells = _merge_into_cells(lines)
    rows = _rows(cells)
    rows, first_grid = _drop_lead(rows, content, excluded)
    rows = _drop_label_column(rows, excluded)
    return _count(rows, content, excluded, exhibits=len(exhibits))


def _body_lines(page, content) -> list[Cell]:
    """Every body-size line inside the content area, with its type size."""
    seen: dict[tuple, float] = {}
    for span in _collect_spans(page):
        if span["size"] <= APPARATUS_MAX_PT:
            continue
        box = span["line_bbox"]
        if not _intersects(box, content):
            continue
        seen[box] = max(seen.get(box, 0.0), span["size"])
    return [Cell(box=box, size_pt=size) for box, size in
            sorted(seen.items(), key=lambda item: (item[0][1], item[0][0]))]


def _drop_title(lines: list[Cell], excluded: list[str]) -> tuple[list[Cell], float]:
    """Remove the page's headline - the largest type on it."""
    largest = max((line.size_pt for line in lines), default=0.0)
    kept = [line for line in lines if line.size_pt < largest - 0.6]
    if len(kept) < len(lines) and kept:
        excluded.append("title")
        return kept, largest
    return lines, largest


def _drop_column_header(lines: list[Cell], excluded: list[str]) -> list[Cell]:
    """
    Remove a grid's column headers, before anything is merged.

    Done first because a header and the row under it merge into one cell
    otherwise, and then neither can be told from the other. A header is set
    larger than the rows it heads *and* has more than one row under it - one
    row under a larger line is a card heading over its body.
    """
    rows = _rows(lines)
    for index in range(len(rows) - HEADER_MIN_ROWS_BELOW):
        row = rows[index]
        following = rows[index + 1]
        if len(row) < 2 or len(following) < 2:
            continue
        if max(c.size_pt for c in row) >= max(c.size_pt for c in following) \
                + HEADER_SIZE_STEP_PT:
            excluded.append("header")
            header = {id(cell) for cell in row}
            return [line for line in lines if id(line) not in header]
        break
    return lines


def _merge_into_cells(lines: list[Cell]) -> list[Cell]:
    """
    Merge lines into the cells they belong to.

    Two rules. Lines close together in the same column are one paragraph.
    A smaller line under a larger one, sharing its column, is a body under its
    heading - which is one card, and the reason a single vertical threshold
    cannot serve both a table and a card row.
    """
    cells: list[Cell] = []
    for line in lines:
        for index, cell in enumerate(cells):
            if _same_cell(cell, line):
                cells[index] = Cell(box=_union(cell.box, line.box),
                                    size_pt=max(cell.size_pt, line.size_pt))
                break
        else:
            cells.append(line)

    changed = True
    while changed:
        changed = False
        for i in range(len(cells)):
            for j in range(len(cells) - 1, i, -1):
                if _same_cell(cells[i], cells[j]):
                    cells[i] = Cell(box=_union(cells[i].box, cells[j].box),
                                    size_pt=max(cells[i].size_pt, cells[j].size_pt))
                    cells.pop(j)
                    changed = True
            if changed:
                break
    return cells


def _same_cell(a: Cell, b: Cell) -> bool:
    if _intersects(a.box, b.box, CELL_MERGE_X_PT, CELL_MERGE_Y_PT):
        return True
    if abs(a.size_pt - b.size_pt) < HEADING_SIZE_STEP_PT:
        return False
    if not _intersects(a.box, b.box, 0.0, HEADING_MERGE_Y_PT):
        return False
    overlap = min(a.box[2], b.box[2]) - max(a.box[0], b.box[0])
    narrower = min(a.box[2] - a.box[0], b.box[2] - b.box[0])
    return narrower > 0 and overlap / narrower >= HEADING_OVERLAP


def _drop_lead(rows: list[list[Cell]], content,
               excluded: list[str]) -> tuple[list[list[Cell]], Optional[int]]:
    """
    Remove the lead paragraph above the grid.

    A full-width line *before* the grid is a lead-in; the same line after it is
    a summary banner, which is a block - reference page 95 has both.
    """
    content_width = max(1.0, content[2] - content[0])
    first_grid = next((index for index, row in enumerate(rows) if len(row) > 1), None)
    if first_grid is None:
        return rows, None

    kept = []
    for index, row in enumerate(rows):
        span = (max(c.box[2] for c in row) - min(c.box[0] for c in row)) / content_width
        if index < first_grid and len(row) == 1 and span >= FULL_WIDTH_SPAN:
            excluded.append("lead")
            continue
        kept.append(row)
    return kept, first_grid


def _drop_label_column(rows: list[list[Cell]],
                       excluded: list[str]) -> list[list[Cell]]:
    """
    Remove a grid's row labels.

    The mirror of the column-header rule: a label is short and much narrower
    than the columns it labels, and it only exists where there is more than
    one row to label. Reference page 12's "Optical fiber" and page 40's
    "Telecommunications" are labels; a first column of prose is not.
    """
    grid = [row for row in rows if len(row) >= 2]
    if len(grid) < 2:
        return rows

    first = [row[0] for row in grid]
    rest_widths = [c.box[2] - c.box[0] for row in grid for c in row[1:]]
    if not rest_widths:
        return rows

    label_width = sum(c.box[2] - c.box[0] for c in first) / len(first)
    other_width = sorted(rest_widths)[len(rest_widths) // 2]
    if label_width <= 0 or other_width / label_width < LABEL_COLUMN_WIDTH_RATIO:
        return rows

    excluded.append("row labels")
    label_ids = {id(cell) for cell in first}
    return [[cell for cell in row if id(cell) not in label_ids] or row
            for row in rows]


def _rows(cells: Sequence[Cell]) -> list[list[Cell]]:
    """Cells grouped into the rows they share."""
    rows: list[list[Cell]] = []
    for cell in sorted(cells, key=lambda c: c.box[1]):
        for row in rows:
            if any(min(other.box[3], cell.box[3])
                   - max(other.box[1], cell.box[1]) > 0 for other in row):
                row.append(cell)
                break
        else:
            rows.append([cell])
    for row in rows:
        row.sort(key=lambda c: c.box[0])
    return rows


def _count(rows: list[list[Cell]], content, excluded: list[str],
           exhibits: int = 0) -> PageStructure:
    """Turn rows of cells into a block count, plus one per exhibit."""
    content_width = max(1.0, content[2] - content[0])
    blocks = exhibits
    band_rows = 0
    widest = 0
    for row in rows:
        widest = max(widest, len(row))
        if _is_band(row, content_width):
            blocks += 1
            band_rows += 1
        else:
            blocks += len(row)
    return PageStructure(
        block_count=blocks, rows=len(rows), columns=widest,
        band_rows=band_rows, excluded=tuple(excluded), exhibits=exhibits,
    )


def _is_band(row: Sequence[Cell], content_width: float) -> bool:
    """
    Whether a row is one band rather than several cells.

    A narrow label column beside a wide prose column is a band - reference
    page 40, when its labels have not already been dropped. Columns of similar
    width are a matrix, and each cell is its own block - reference page 12.
    The distinction is the width ratio, because geometrically the two are the
    same arrangement.
    """
    if not 2 <= len(row) <= BAND_MAX_CELLS:
        return False
    span = (max(c.box[2] for c in row) - min(c.box[0] for c in row)) / content_width
    if span < BAND_MIN_SPAN:
        return False
    widths = sorted(max(1.0, cell.box[2] - cell.box[0]) for cell in row)
    return widths[-1] / widths[0] >= BAND_MIN_WIDTH_RATIO


def count_blocks_in_pdf(pdf_path: str,
                        pages: Optional[Sequence[int]] = None) -> list[PageStructure]:
    """Structure for every page of a PDF, or for the given 1-based pages."""
    import fitz

    doc = fitz.open(pdf_path)
    try:
        wanted = list(pages) if pages is not None else list(range(1, len(doc) + 1))
        return [count_blocks(doc[n - 1]) for n in wanted if 1 <= n <= len(doc)]
    finally:
        doc.close()
