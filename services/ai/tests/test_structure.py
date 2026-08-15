"""
Counting the blocks on a page the way a reader would.

``page_metrics.block_count`` clusters content at a fixed threshold, which is
the right measure for "is this page denser than that one" and the wrong one for
learning a template's budget: "17.4 clusters per slide" is not something a
planner can be held to. ``measure.structure`` counts cells of the page's grid
instead, and the rules that make one number out of three different-looking
pages are what these tests pin down.

The pages here are synthetic, drawn with PyMuPDF at known coordinates, so the
arrangement under test is stated rather than inferred. The same counter is
checked against the reference deck's real pages in ``test_style_fidelity`` -
against counts committed to the fixture, so that check needs no PDF either.
"""

import fitz
import pytest

from app.measure.structure import count_blocks

WIDTH, HEIGHT = 960.0, 540.0
LEFT = WIDTH * 0.05
RIGHT = WIDTH * 0.95
BODY_PT = 12.0


def _page(doc=None):
    doc = doc or fitz.open()
    return doc, doc.new_page(width=WIDTH, height=HEIGHT)


def _write(page, text: str, x: float, y: float, size: float = BODY_PT) -> None:
    page.insert_text((x, y), text, fontsize=size, fontname="helv")


def _title(page, text: str = "A headline that states a finding") -> None:
    _write(page, text, LEFT, HEIGHT * 0.12, size=25.0)


def _lines(page, text: str, x: float, y: float, count: int,
           size: float = BODY_PT, leading: float = 13.0) -> None:
    """Several wrapped lines of one paragraph, which is one cell."""
    for index in range(count):
        _write(page, f"{text} {index}", x, y + index * leading, size=size)


class TestAMatrixCountsEveryCell:
    """Reference page 12: three columns of similar width, each cell a
    complete sentence, and every cell is its own block."""

    @pytest.fixture
    def matrix(self):
        doc, page = _page()
        _title(page)
        column_width = (RIGHT - LEFT) / 3
        for row in range(5):
            for column in range(3):
                _lines(page, f"A sentence in cell {row}{column} of the matrix",
                       LEFT + column * column_width, 160 + row * 60, count=2)
        yield page
        doc.close()

    def test_every_cell_is_a_block(self, matrix):
        assert count_blocks(matrix).block_count == 15

    def test_the_grid_shape_is_reported(self, matrix):
        structure = count_blocks(matrix)

        assert (structure.rows, structure.columns) == (5, 3)

    def test_the_title_is_not_one_of_them(self, matrix):
        assert "title" in count_blocks(matrix).excluded


class TestARowBandIsOneBlock:
    """Reference page 40: a narrow label beside a wide sentence is one band,
    not two cells - geometrically the same arrangement as a matrix row, told
    apart by the width ratio."""

    def test_seven_bands_count_as_seven(self):
        doc, page = _page()
        _title(page)
        for row in range(7):
            y = 150 + row * 48
            _write(page, f"Label {row}", LEFT, y)
            _lines(page, "A full sentence of prose carrying the point for this "
                         "band, long enough to read as a claim",
                   LEFT + 150, y, count=2)

        structure = count_blocks(page)

        assert structure.block_count == 7
        doc.close()

    def test_a_single_band_is_one_block_not_two_cells(self):
        """With one row there is no label *column* to recognise, so the band
        rule is what has to carry it."""
        doc, page = _page()
        _title(page)
        _write(page, "Label", LEFT, 200)
        _lines(page, "A full sentence of prose carrying the point for this "
                     "band, long enough to read as a claim on its own",
               LEFT + 150, 200, count=2)

        structure = count_blocks(page)

        assert structure.block_count == 1
        assert structure.band_rows == 1
        doc.close()

    def test_columns_of_similar_width_are_not_bands(self):
        doc, page = _page()
        _title(page)
        half = (RIGHT - LEFT) / 2
        for row in range(4):
            y = 160 + row * 60
            _lines(page, "A sentence in the left column of this row",
                   LEFT, y, count=2)
            _lines(page, "A sentence in the right column of this row",
                   LEFT + half, y, count=2)

        structure = count_blocks(page)

        assert structure.block_count == 8
        assert structure.band_rows == 0
        doc.close()


class TestGridFurnitureIsNotContent:
    def test_a_column_header_over_several_rows_is_dropped(self):
        doc, page = _page()
        _title(page)
        column_width = (RIGHT - LEFT) / 3
        for column in range(3):
            _write(page, f"Column {column}", LEFT + column * column_width, 150,
                   size=BODY_PT + 3)
        for row in range(3):
            for column in range(3):
                _lines(page, f"A sentence in row {row} column {column}",
                       LEFT + column * column_width, 200 + row * 60, count=2)

        structure = count_blocks(page)

        assert structure.block_count == 9
        assert "header" in structure.excluded
        doc.close()

    def test_a_card_heading_over_its_own_body_is_not_a_header(self):
        """The same shape with one row under it is a card, and the heading
        belongs to it."""
        doc, page = _page()
        _title(page)
        column_width = (RIGHT - LEFT) / 3
        for column in range(3):
            x = LEFT + column * column_width
            _write(page, f"Card {column}", x, 200, size=BODY_PT + 4)
            _lines(page, "The card's own paragraph, which belongs to it",
                   x, 214, count=3)

        structure = count_blocks(page)

        assert structure.block_count == 3
        assert "header" not in structure.excluded
        doc.close()

    def test_a_narrow_label_column_beside_wide_ones_is_dropped(self):
        doc, page = _page()
        _title(page)
        label_width = 110
        column_width = (RIGHT - LEFT - label_width) / 2
        for row in range(4):
            y = 160 + row * 60
            _write(page, f"Row {row}", LEFT, y)
            for column in range(2):
                _lines(page, f"A sentence of prose in column {column} of this row",
                       LEFT + label_width + column * column_width, y, count=2)

        structure = count_blocks(page)

        assert structure.block_count == 8
        assert "row labels" in structure.excluded
        doc.close()

    def test_a_lead_paragraph_above_the_grid_is_not_a_block(self):
        doc, page = _page()
        _title(page)
        _lines(page, "A standing paragraph introducing what the grid below shows, "
                     "set across the full width of the page the way a lead-in is, "
                     "and carrying no finding of its own",
               LEFT, 150, count=2)
        column_width = (RIGHT - LEFT) / 3
        for row in range(3):
            for column in range(3):
                _lines(page, f"A sentence in cell {row}{column}",
                       LEFT + column * column_width, 240 + row * 60, count=2)

        structure = count_blocks(page)

        assert structure.block_count == 9
        assert "lead" in structure.excluded
        doc.close()


class TestApparatusIsNotContent:
    def test_a_footnote_is_too_small_to_be_a_block(self):
        doc, page = _page()
        _title(page)
        column_width = (RIGHT - LEFT) / 3
        for column in range(3):
            _lines(page, f"A sentence in cell {column}",
                   LEFT + column * column_width, 200, count=2)
        _write(page, "1. A footnote resolving a marker in the copy above",
               LEFT, HEIGHT - 40, size=7.0)
        _write(page, "Source: an organisation, 2026", LEFT, HEIGHT - 28, size=7.0)

        assert count_blocks(page).block_count == 3
        doc.close()


class TestAPageWithNothingOnIt:
    def test_an_empty_page_counts_nothing(self):
        doc, page = _page()

        structure = count_blocks(page)

        assert structure.block_count == 0
        assert (structure.rows, structure.columns) == (0, 0)
        doc.close()

    def test_a_cover_carries_one_block_at_most(self):
        doc, page = _page()
        _title(page, "The title of the whole deck")
        _lines(page, "A standfirst under it", LEFT, 220, count=2)

        assert count_blocks(page).block_count <= 1
        doc.close()
