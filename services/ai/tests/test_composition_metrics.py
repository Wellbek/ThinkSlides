"""
Metrics that can see composition, and the gate that proves they can.

The finding these exist for: nine of the eleven style metrics in the old
scorecard are permutation-invariant. Shuffle every element on a page and ink
coverage, word count, the type ramp and the palette come back identical - so
a scrambled reference page scores exactly as well as the real one, and no
change to the layout engine can be shown to have helped or hurt.

The gate is deliberately hard to pass and two candidate metrics failed it
during development. `reading_order` compared a page's content-stream order
to its visual order, which is not a property a reference PDF has, and it
scored 0.000 on real pages. `whitespace_evenness` separated a real page from
a scrambled one by 0.084 against a required 0.10. Both were deleted rather
than tuned, because tuning a metric against the test meant to validate it
produces a number that agrees with you and nothing else.
"""
import os

import pytest

from app.eval.composition import (
    METRIC_NAMES,
    Baseline,
    Element,
    PageObservation,
    alignment_score,
    baseline_rhythm,
    measure_page,
)
from app.eval.discriminate import (
    REQUIRED_MARGIN,
    discriminate,
    jitter,
    shuffle_positions,
)
from app.measure.rhythm import columns_from_page, recover_rhythm

EY_PDF = os.path.expanduser(
    "~/Downloads/ey-inaugural-ey-iif-global-insurance-risk-management-survey-march23.pdf"
)


def _aligned_page() -> PageObservation:
    """Four blocks on two shared edges, on a 21pt rhythm - a composed page."""
    elements = []
    for index in range(4):
        top = 0.1 + index * 0.2
        elements.append(Element(0.1, top, 0.45, top + 0.15, "text"))
        elements.append(Element(0.55, top, 0.9, top + 0.15, "text"))
    columns = []
    for column in range(2):
        base = 100.0 + column * 0.0
        columns.append(tuple(
            Baseline(base + line * 21.0, 16.0) for line in range(8)
        ))
    flat = tuple(b for column in columns for b in column)
    return PageObservation(960.0, 540.0, tuple(elements), flat, tuple(columns))


class TestScramblingIsContentPreserving:
    """The scrambles must change only composition, or the gate proves nothing."""

    def test_a_scramble_keeps_every_element_and_its_size(self):
        page = _aligned_page()
        moved = shuffle_positions(page, seed=1)
        assert len(moved.elements) == len(page.elements)
        before = sorted(round(e.area, 9) for e in page.elements)
        after = sorted(round(e.area, 9) for e in moved.elements)
        assert before == after

    def test_the_quantities_the_old_metrics_read_are_unchanged(self):
        """This is the whole finding, as an assertion.

        Total ink, element count and every size on the page survive a
        scramble untouched - so any metric built from them cannot tell the
        two pages apart, however differently they read.
        """
        page = _aligned_page()
        moved = jitter(page, seed=3)
        assert sum(e.area for e in page.elements) == pytest.approx(
            sum(e.area for e in moved.elements), abs=1e-9
        )
        assert len(page.baselines) == len(moved.baselines)
        assert sorted(b.size_pt for b in page.baselines) == sorted(
            b.size_pt for b in moved.baselines
        )

    def test_but_the_composition_metrics_do_move(self):
        page = _aligned_page()
        moved = jitter(page, seed=3)
        assert alignment_score(moved) < alignment_score(page)


class TestAlignment:
    def test_shared_edges_score_higher_than_scattered_ones(self):
        aligned = _aligned_page()
        scattered = PageObservation(
            960.0, 540.0,
            tuple(
                Element(0.05 + i * 0.037, 0.1 + i * 0.09,
                        0.05 + i * 0.037 + 0.3, 0.1 + i * 0.09 + 0.12)
                for i in range(8)
            ),
        )
        assert alignment_score(aligned) > alignment_score(scattered)

    def test_a_page_with_nothing_on_it_scores_zero(self):
        assert alignment_score(PageObservation(960.0, 540.0)) == 0.0


class TestBaselineRhythm:
    def test_text_on_the_unit_scores_near_one(self):
        page = _aligned_page()
        assert baseline_rhythm(page, 21.0) > 0.9

    def test_text_off_the_unit_scores_low(self):
        page = _aligned_page()
        broken = PageObservation(
            page.width_pt, page.height_pt, page.elements,
            tuple(Baseline(b.position_pt + (i % 5) * 3.1, b.size_pt)
                  for i, b in enumerate(page.baselines)),
            tuple(
                tuple(Baseline(b.position_pt + (i % 5) * 3.1, b.size_pt)
                      for i, b in enumerate(column))
                for column in page.baseline_columns
            ),
        )
        assert baseline_rhythm(broken, 21.0) < baseline_rhythm(page, 21.0)

    def test_columns_are_measured_separately(self):
        """Two columns each on the rhythm but offset from each other are both
        rhythmic. Pooling and sorting them manufactures gaps belonging to
        neither, which reads a strict page as keeping no rhythm at all - the
        bug that made this metric fail its own gate during development."""
        offset = PageObservation(
            960.0, 540.0, _aligned_page().elements,
            (),
            (
                tuple(Baseline(100.0 + i * 21.0, 16.0) for i in range(8)),
                tuple(Baseline(107.5 + i * 21.0, 16.0) for i in range(8)),
            ),
        )
        # Rebuild the flat list from the columns so the dominant size resolves.
        offset = PageObservation(
            offset.width_pt, offset.height_pt, offset.elements,
            tuple(b for c in offset.baseline_columns for b in c),
            offset.baseline_columns,
        )
        assert baseline_rhythm(offset, 21.0) > 0.9


class TestTheCompositeOnlyCountsValidatedMetrics:
    def test_deleted_metrics_are_not_scored(self):
        assert "reading_order" not in METRIC_NAMES
        assert "whitespace_evenness" not in METRIC_NAMES

    def test_a_metric_that_does_not_apply_is_absent_rather_than_zero(self):
        page = PageObservation(960.0, 540.0, _aligned_page().elements)
        score = measure_page(page)
        assert "grid_conformance" not in score.usable
        assert score.composite == score.composite  # not NaN: alignment applies


@pytest.mark.skipif(not os.path.exists(EY_PDF),
                    reason="reference PDF not present; see app/eval/corpus.py")
class TestTheGateOnARealReference:
    @pytest.fixture(scope="class")
    def setup(self):
        import fitz

        from app.eval.composition import observe_page
        from app.measure.grid import edges_from_boxes, recover_grid

        page = fitz.open(EY_PDF)[3]
        obs = observe_page(page)
        width = page.rect.width
        boxes = [
            b["bbox"] for b in page.get_text("dict")["blocks"]
            if b.get("type") == 0 and (b["bbox"][2] - b["bbox"][0]) > 0.02 * width
        ]
        grid = recover_grid(edges_from_boxes(boxes, width))
        rhythm = recover_rhythm(columns_from_page(page))
        return obs, grid, rhythm

    def test_the_reference_keeps_a_recoverable_rhythm(self, setup):
        _, _, rhythm = setup
        assert rhythm is not None
        assert rhythm.unit_pt == pytest.approx(20.95, abs=0.3)
        assert rhythm.confidence > 0.9

    def test_every_scored_metric_separates_the_page_from_a_scramble(self, setup):
        obs, grid, rhythm = setup
        result = discriminate(obs, grid=grid, baseline_pt=rhythm.unit_pt)
        assert not result.failing, (
            "these metrics cannot tell a designed page from a scrambled one "
            f"and must be deleted, not tuned: {result.failing}\n"
            + result.to_markdown()
        )
        for verdict in result.verdicts:
            assert verdict.margin >= REQUIRED_MARGIN
