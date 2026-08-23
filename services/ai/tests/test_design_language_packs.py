"""
Reading a publication into a Design Language Pack.

The gate this file exists for: **a pack has to be distinguishable by our own
metric.** If a deck measured under the EY pack does not score better than the
same deck measured under Blackstone's, then whatever ingestion extracted is
decoration - it looks like a description of the publication and steers
nothing.

The other half is the refusals. Grid, rhythm and spacing recovery are all
period estimation, and period estimation always returns an answer. A pack
that reports a confident grid for a deck that has none teaches every later
stage to converge on a fiction, so a pack states its confidence next to
every reading and says plainly when it recovered nothing.

The reference PDFs are deliberately not committed (see `app/eval/corpus.py`),
so the tests that need them skip when they are not on the machine. The packs
they produce *are* committed, because a pack is meant to be read and
corrected by a designer.
"""
import json
import os

import pytest

from app.design.pack import Pack
from app.eval import corpus

PACK_DIR = os.path.join("app", "design", "packs")
PACK_IDS = ("ey-iif-risk", "bxsl-investor", "wti-2025")


def _load(pack_id: str) -> Pack:
    with open(os.path.join(PACK_DIR, f"{pack_id}.json")) as handle:
        return Pack.model_validate(json.load(handle))


def _have_pdfs() -> bool:
    return all(corpus.find_exemplar_pdf(p) for p in PACK_IDS)


class TestTheCommittedPacksAreWellFormed:
    """These run everywhere - the packs are committed, the PDFs are not."""

    @pytest.mark.parametrize("pack_id", PACK_IDS)
    def test_the_pack_loads_and_names_its_source(self, pack_id):
        pack = _load(pack_id)
        assert pack.id == pack_id
        assert pack.source_note

    @pytest.mark.parametrize("pack_id", PACK_IDS)
    def test_it_has_a_body_role_and_a_headline(self, pack_id):
        pack = _load(pack_id)
        assert "body" in pack.type_roles
        assert pack.role("body").size_pt > 0
        assert pack.role("body").line_height_pt >= pack.role("body").size_pt

    @pytest.mark.parametrize("pack_id", PACK_IDS)
    def test_every_reading_carries_its_confidence(self, pack_id):
        pack = _load(pack_id)
        assert pack.grid_confidence.note
        assert pack.rhythm.confidence.note
        assert pack.spacing.confidence.note

    @pytest.mark.parametrize("pack_id", PACK_IDS)
    def test_its_legibility_floor_permits_its_own_body_copy(self, pack_id):
        """The global 16pt floor this replaces is 3.0% of a 540pt canvas, and
        every one of these publications sets body copy below that."""
        pack = _load(pack_id)
        floor = pack.policy.floor_pt("body", pack.canvas)
        assert floor <= pack.role("body").size_pt

    @pytest.mark.parametrize("pack_id", PACK_IDS)
    def test_it_carries_archetypes_with_frames(self, pack_id):
        pack = _load(pack_id)
        assert pack.archetypes
        for archetype in pack.archetypes:
            assert archetype.frames, f"{archetype.id} has no frames"

    def test_the_packs_are_actually_different_from_each_other(self):
        """Three publications, three design languages. If ingestion returned
        the same numbers for all of them it read nothing."""
        packs = [_load(p) for p in PACK_IDS]
        assert len({p.role("body").size_pt for p in packs}) > 1
        assert len({round(p.rhythm.unit_pt, 1) for p in packs}) > 1
        assert len({p.grid.columns for p in packs}) > 1


@pytest.mark.skipif(not _have_pdfs(),
                    reason="reference PDFs not present; see app/eval/corpus.py")
class TestThePackCapturesItsOwnPublication:
    """The gate. A pack that does not fit its own publication best is
    decoration, however plausible its numbers look."""

    @pytest.fixture(scope="class")
    def diagonal(self):
        import fitz

        from app.eval.composition import GridView, measure_page, observe_page

        packs = {p: _load(p) for p in PACK_IDS}
        table: dict[str, dict[str, float]] = {}
        for entry in corpus.EXEMPLARS:
            path = corpus.find_exemplar_pdf(entry.id)
            doc = fitz.open(path)
            row = {}
            for pack_id, pack in packs.items():
                view = GridView.from_pack(pack.grid, pack.canvas)
                scores = [
                    measure_page(observe_page(doc[page - 1]), grid=view,
                                 baseline_pt=pack.rhythm.unit_pt).composite
                    for page in entry.design_pages
                ]
                row[pack_id] = sum(scores) / len(scores)
            table[entry.id] = row
        return table

    @pytest.mark.parametrize("pack_id", PACK_IDS)
    def test_each_reference_scores_best_under_its_own_pack(self, diagonal, pack_id):
        row = diagonal[pack_id]
        best = max(row, key=row.get)
        assert best == pack_id, (
            f"{pack_id} scores best under {best}'s pack, so its own pack is "
            f"not capturing what makes it itself: {row}"
        )

    @pytest.mark.parametrize("pack_id", PACK_IDS)
    def test_and_by_a_margin_worth_having(self, diagonal, pack_id):
        row = dict(diagonal[pack_id])
        own = row.pop(pack_id)
        assert own - max(row.values()) >= 0.04, row


@pytest.mark.skipif(not _have_pdfs(),
                    reason="reference PDFs not present; see app/eval/corpus.py")
class TestIngestionRefusesRatherThanInvents:
    def test_a_deck_whose_pages_share_no_grid_says_so(self):
        """No single grid fits all five of EY's design pages - an executive
        spread and a chart page are not on the same columns. The pack reports
        the grid its pages agree on and how many agreed, rather than fitting
        one lattice to all of them and calling the residual a grid."""
        pack = _load("ey-iif-risk")
        assert "agreed by" in pack.grid_confidence.note

    def test_a_deck_with_no_spacing_ladder_is_not_given_one(self):
        """Blackstone's gaps do not sit on a ladder, and the pack says the
        base it carries was derived rather than measured."""
        pack = _load("bxsl-investor")
        assert pack.spacing.confidence.value == 0.0
        assert "derived" in pack.spacing.confidence.note

    def test_the_recovered_body_size_matches_the_measured_profile(self):
        """Read independently by `app/measure/deck_metrics.py` when the
        exemplar fixtures were built."""
        expected = {"ey-iif-risk": 16.0, "bxsl-investor": 12.0, "wti-2025": 10.0}
        for pack_id, size in expected.items():
            assert _load(pack_id).role("body").size_pt == pytest.approx(size, abs=0.6)
