"""
Tables have to be laid out from their content, not split evenly.

Issue #56's G7: every column got an equal share of the band and every row an
equal share of the height, whatever they held. "Channel Development" and
"1.8" rendered in identical widths, figures were left-aligned like prose,
there was no total-row emphasis, and a four-row table stretched over four
inches came out with visibly uneven rows.
"""

import pytest

from app.renderer import table_layout
from app.renderer.table_layout import is_numeric_cell, plan_table

SEGMENTS = [
    ["Segment", "Revenue ($B)", "Op. margin", "Stores"],
    ["North America", "26.8", "18.4%", "18,400"],
    ["International", "7.6", "11.2%", "21,900"],
    ["Channel Development", "1.8", "42.1%", "-"],
    ["Total", "36.2", "14.9%", "40,300"],
]


@pytest.fixture
def layout():
    return plan_table(SEGMENTS, has_header=True, region_width_in=11.63, region_height_in=4.55)


class TestNumericDetection:
    @pytest.mark.parametrize("value", [
        "26.8", "18.4%", "18,400", "$1,200", "-3.2", "(450)", "1.5x", "12 000",
    ])
    def test_figures_are_recognised(self, value):
        assert is_numeric_cell(value)

    @pytest.mark.parametrize("value", [
        "North America", "Q1 revenue", "", "-", "n/a", "TBD", "2021-2025",
    ])
    def test_prose_and_placeholders_are_not(self, value):
        assert not is_numeric_cell(value)

    def test_a_placeholder_does_not_disqualify_a_numeric_column(self, layout):
        """One "-" standing in for a missing store count was enough to make
        an otherwise all-numeric column render left-aligned like prose."""
        assert 3 in layout.numeric_columns

    def test_the_label_column_stays_prose(self, layout):
        assert 0 not in layout.numeric_columns

    def test_all_figure_columns_are_marked(self, layout):
        assert layout.numeric_columns == frozenset({1, 2, 3})


class TestColumnWidths:
    def test_a_long_text_column_gets_more_room_than_a_short_numeric_one(self, layout):
        assert layout.col_fractions[0] > layout.col_fractions[1]

    def test_widths_fill_the_region_exactly(self, layout):
        assert sum(layout.col_fractions) == pytest.approx(1.0)

    def test_no_column_collapses_to_a_sliver(self):
        """Proportional-only allocation squeezes a narrow column until its own
        header wraps."""
        wide = [
            ["A very long descriptive label indeed, quite long", "n"],
            ["Another extremely long descriptive label here too", "1"],
        ]
        plan = plan_table(wide, has_header=True, region_width_in=11.63, region_height_in=4.55)

        assert min(plan.col_fractions) >= 0.075


class TestRowHeights:
    def test_the_table_takes_only_the_height_it_needs(self, layout):
        """Splitting the band equally per row is what stretched a four-row
        table across four inches."""
        assert layout.height_in < 4.55 * 0.75

    def test_every_single_line_row_is_the_same_height(self, layout):
        body = layout.row_heights_in[1:]
        assert max(body) == pytest.approx(min(body))

    def test_a_wrapping_cell_makes_its_row_taller(self):
        rows = [
            ["Item", "Note"],
            ["A", "short"],
            ["B", "a considerably longer note that has to wrap onto several "
                  "lines because the column simply is not wide enough for it"],
        ]
        plan = plan_table(rows, has_header=True, region_width_in=6.0, region_height_in=4.55)

        assert plan.row_heights_in[2] > plan.row_heights_in[1]

    def test_a_long_table_shrinks_its_type_to_fit(self):
        rows = [["Metric", "Value"]] + [[f"Row {i}", str(i)] for i in range(24)]
        plan = plan_table(rows, has_header=True, region_width_in=11.63, region_height_in=4.55)

        assert plan.body_pt < 14.0
        assert plan.height_in <= 4.55 + 1e-6

    def test_an_impossible_table_is_compressed_rather_than_overflowing(self):
        rows = [["Metric", "Value"]] + [[f"Row {i}", str(i)] for i in range(80)]
        plan = plan_table(rows, has_header=True, region_width_in=11.63, region_height_in=4.55)

        assert plan.body_pt == table_layout.MIN_CELL_PT
        assert plan.height_in <= 4.55 + 1e-6


class TestTotalRow:
    def test_a_trailing_summary_row_is_found(self, layout):
        assert layout.total_row_index == 4

    def test_a_table_without_one_reports_none(self):
        rows = [["A", "B"], ["1", "2"], ["3", "4"]]
        plan = plan_table(rows, has_header=True, region_width_in=8.0, region_height_in=3.0)

        assert plan.total_row_index is None

    def test_a_two_row_table_is_not_read_as_header_plus_total(self):
        """Otherwise a single data row labelled "Total" would be both."""
        rows = [["Metric", "Value"], ["Total", "10"]]
        plan = plan_table(rows, has_header=True, region_width_in=8.0, region_height_in=3.0)

        assert plan.total_row_index is None


class TestDegenerateInput:
    def test_no_rows_is_no_table(self):
        assert plan_table([], has_header=True, region_width_in=8.0, region_height_in=3.0) is None

    def test_rows_with_no_cells_is_no_table(self):
        assert plan_table([[], []], has_header=True,
                          region_width_in=8.0, region_height_in=3.0) is None

    def test_ragged_rows_are_padded_rather_than_dropped(self):
        rows = [["A", "B", "C"], ["1"], ["2", "3"]]
        plan = plan_table(rows, has_header=True, region_width_in=8.0, region_height_in=3.0)

        assert len(plan.col_fractions) == 3
        assert len(plan.row_heights_in) == 3


# Six columns in a half-width slot, from the Kubernetes run (#351). One-word
# labels sit beside sentences, so proportional widths squeezed the label
# columns below their own words and "Strategy" was set as "Stra / tegy".
MATRIX = [
    ["Strategy", "Spare capacity", "Rollback pattern", "Operational burden",
     "Ingress fit", "Recommended use"],
    ["Rolling", "Low", "Prior revision; reverse partial exposure", "Low",
     "Native Service", "Routine releases; strong health checks"],
    ["Blue-green", "High", "Switch routing back to prior version", "Medium",
     "Service / ingress cutover", "Clean boundary; rapid reversal"],
    ["Argo Rollouts", "Depends on strategy",
     "Analysis, pauses, promotion and rollback controls", "Medium-high",
     "Ingress integrations supported", "Kubernetes-native progressive delivery"],
]


class TestNoWordIsBrokenAcrossLines:
    def test_every_column_is_at_least_as_wide_as_its_longest_word(self):
        width_in = 5.73
        layout = plan_table(MATRIX, True, width_in, 4.0)
        for col, fraction in enumerate(layout.col_fractions):
            for index, row in enumerate(MATRIX):
                size = layout.header_pt if index == 0 else layout.body_pt
                for word in row[col].split():
                    need = (table_layout._word_width_pt(word, size, index == 0)
                            + table_layout.CELL_PAD_H_PT)
                    assert fraction * width_in * 72.0 >= need - 0.5, (col, word)


class TestTheHeightATableNeeds:
    def test_wrapping_cells_make_a_table_taller_than_its_row_count(self):
        """A floor from the row count alone let a table whose cells wrap onto
        three lines be set in a band a third of its height."""
        wide = table_layout.needed_height_in(MATRIX, True, 11.7)
        narrow = table_layout.needed_height_in(MATRIX, True, 5.7)
        assert narrow > wide * 1.3

    def test_it_is_measured_at_a_legible_size_and_never_compressed(self):
        layout = plan_table(MATRIX, True, 5.7, float("inf"),
                            sizes=(table_layout.LEGIBLE_CELL_PT,))
        assert layout.body_pt == table_layout.LEGIBLE_CELL_PT
        assert table_layout.needed_height_in(MATRIX, True, 5.7) == layout.height_in


class TestTheRowsFitTheLinesDrawnInThem:
    """The plan measured in regular Arial while the image wrapped in the
    design's own face, bold in the header and total rows - so a drawn cell
    could need more lines than its row had, and spilled over its neighbours."""

    @pytest.mark.parametrize("family", ["Arial", "DejaVu Serif", "Liberation Serif"])
    def test_every_drawn_cell_fits_its_planned_row(self, family):
        from app.renderer.typeset.shape import wrap

        rows = [*MATRIX, ["Total", "Varies", "Ingress integrations supported",
                          "Medium-high", "Kubernetes-native progressive delivery",
                          "Pick per service"]]
        width_in = 5.73
        layout = plan_table(rows, True, width_in, float("inf"), family=family)
        assert layout.total_row_index == len(rows) - 1
        for index, row in enumerate(rows):
            header = index == 0
            bold = header or index == layout.total_row_index
            size = layout.header_pt if header else layout.body_pt
            for col, cell in enumerate(row):
                usable = (layout.col_fractions[col] * width_in * 72.0
                          - table_layout.CELL_PAD_H_PT)
                lines = len(wrap(cell, usable, family, size, bold=bold)) or 1
                needed = (lines * size * 1.2) / 72.0
                assert needed <= layout.row_heights_in[index] + 1e-6, (family, cell)

    def test_a_zero_width_region_still_plans(self):
        assert plan_table(MATRIX, True, 0.0, 2.0) is not None
