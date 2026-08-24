"""
Repairing a page without being able to make it worse, and validating it
without being able to skip.

The loop this replaces redrafts the whole slide: one overset paragraph and
the page is rewritten, including the parts that were right. And its
validator was a tool the model could decline to call - `validate_slide_json`
was invoked **zero times across every observed run**, so density,
title-register and apparatus checks were inert in practice. A correctness
check that can be skipped is not a correctness mechanism.

The two properties pinned here are the ones that make the loop safe to run
unattended: a revision lands only if the page's score falls, and the patch
vocabulary is closed so a repair nobody can account for cannot be applied.
"""
import json
import os

import pytest

from app.design.bind import build_page
from app.design.material import shape_from
from app.design.pack import Pack
from app.design.repair import (
    PATCHES,
    apply_patch,
    diagnose,
    repair,
    score,
)
from app.design.schema import (
    DesignDoc,
    FracPlace,
    Frame,
    Page,
    PackRef,
    Paragraph,
    PatchOp,
    Run,
    TextBlock,
)
from app.design.select import choose

PACK_PATH = os.path.join("app", "design", "packs", "ey-iif-risk.json")

LONG = [
    "The results of our inaugural survey of insurance industry CROs "
    "demonstrate the expanding and diversifying risks insurers face now.",
    "Many of today's most pressing risks are related to the profound shifts "
    "reshaping the industry and rising regulatory requirements.",
    "That is true not only for near-term risks, but also for devising plans "
    "for risks forming on the horizon.",
    "Some observers describe the current moment as a polycrisis, given high "
    "market volatility.",
    "But our research also presents reasons for optimism, and growth is "
    "projected at 3% for 2026.",
]


@pytest.fixture(scope="module")
def pack() -> Pack:
    with open(PACK_PATH) as handle:
        return Pack.model_validate(json.load(handle))


def _overset_doc(pack: Pack) -> tuple[DesignDoc, Page]:
    """A page whose copy plainly does not fit the frame it is bound to."""
    page = Page(
        id="p", archetype="test", frames=(
            Frame(id="tiny", role="column",
                  place=FracPlace(x=0.1, y=0.1, w=0.2, h=0.05),
                  content=(TextBlock(
                      type_role="body",
                      paragraphs=tuple(
                          Paragraph(runs=(Run(text=line),)) for line in LONG
                      ),
                  ),)),
        ),
    )
    doc = DesignDoc(pack=PackRef(id=pack.id), canvas=pack.canvas,
                    grid=pack.grid, pages=(page,))
    return doc, page


class TestFindingsAreLocated:
    def test_overflow_names_the_frame_it_is_in(self, pack):
        doc, page = _overset_doc(pack)
        findings = diagnose(doc, page, pack)
        assert findings
        assert any(f.frame_id == "tiny" and f.property == "overflow"
                   for f in findings)

    def test_a_finding_carries_the_evidence_for_itself(self, pack):
        doc, page = _overset_doc(pack)
        finding = next(f for f in diagnose(doc, page, pack)
                       if f.property == "overflow")
        assert finding.observed and finding.expected
        assert "tiny" in finding.describe()

    def test_a_clean_page_produces_no_findings(self, pack):
        page = Page(id="p", archetype="test", frames=(
            Frame(id="roomy", role="column",
                  place=FracPlace(x=0.1, y=0.1, w=0.6, h=0.6),
                  content=(TextBlock(type_role="body", paragraphs=(
                      Paragraph(runs=(Run(text="Short."),)),
                  )),)),
        ))
        doc = DesignDoc(pack=PackRef(id=pack.id), canvas=pack.canvas,
                        grid=pack.grid, pages=(page,))
        assert diagnose(doc, page, pack) == []
        assert score(doc, page, pack) == 0


class TestTheLoopCanOnlyRatchetUpward:
    def test_a_repair_that_does_not_help_is_not_kept(self, pack):
        """Setting copy in more columns is tried, not assumed: a narrower
        measure produces disproportionately more lines, so splitting a 500pt
        column in three takes a block from 360pt deep to 405pt. The gate
        rejecting that is the gate working."""
        doc, page = _overset_doc(pack)
        result = repair(doc, page, pack)
        assert result.after <= result.before
        if not result.applied:
            assert result.page.frames == page.frames

    def test_the_page_is_never_returned_worse_than_it_arrived(self, pack):
        doc, page = _overset_doc(pack)
        result = repair(doc, page, pack)
        assert score(doc, result.page, pack) <= score(doc, page, pack)

    def test_a_generated_page_survives_the_loop(self, pack):
        shape = shape_from("content", "A heading", LONG)
        page = build_page("p1", shape, choose(shape, pack), pack)
        doc = DesignDoc(pack=PackRef(id=pack.id), canvas=pack.canvas,
                        grid=pack.grid, pages=(page,))
        result = repair(doc, page, pack)
        assert result.after <= result.before

    def test_it_is_bounded(self, pack):
        doc, page = _overset_doc(pack)
        assert repair(doc, page, pack, max_rounds=2).rounds <= 2

    def test_what_it_did_is_recorded_on_the_page(self, pack):
        shape = shape_from("content", "A heading", LONG)
        page = build_page("p1", shape, choose(shape, pack), pack)
        doc = DesignDoc(pack=PackRef(id=pack.id), canvas=pack.canvas,
                        grid=pack.grid, pages=(page,))
        result = repair(doc, page, pack)
        assert result.page.provenance.patched_by == result.applied


class TestThePatchVocabularyIsClosed:
    def test_an_unknown_operation_is_refused(self, pack):
        _, page = _overset_doc(pack)
        with pytest.raises(ValueError) as excinfo:
            apply_patch(page, PatchOp(op="rewrite_everything", args={}))
        assert "unknown patch operation" in str(excinfo.value)

    def test_the_refusal_names_what_is_available(self, pack):
        _, page = _overset_doc(pack)
        with pytest.raises(ValueError) as excinfo:
            apply_patch(page, PatchOp(op="nope", args={}))
        for name in PATCHES:
            assert name in str(excinfo.value)

    def test_every_patch_returns_a_valid_page(self, pack):
        _, page = _overset_doc(pack)
        for op, args in (
            ("set_columns", {"frame_id": "tiny", "columns": 2}),
            ("set_z", {"frame_id": "tiny", "z": 3}),
            ("set_ground", {"frame_id": "tiny", "ground": "ground_alt"}),
        ):
            result = apply_patch(page, PatchOp(op=op, args=args))
            assert isinstance(result, Page)

    def test_a_patch_leaves_the_original_untouched(self, pack):
        """Pages are immutable, so a rejected round cannot have half-applied."""
        _, page = _overset_doc(pack)
        apply_patch(page, PatchOp(op="set_z", args={"frame_id": "tiny", "z": 9}))
        assert page.frame_by_id("tiny").z == 0


class TestValidationCannotBeSkipped:
    """The check this replaces was a tool the model never called."""

    def test_an_unplaceable_document_will_not_render(self, pack, tmp_path):
        from app.renderer.designdoc_renderer import InvalidDesignDoc, render_doc

        page = Page(id="p", archetype="test", frames=(
            Frame(id="a", role="panel",
                  place=FracPlace(x=0.0, y=0.0, w=1.0, h=1.0), z=0),
            Frame(id="b", role="panel",
                  place=FracPlace(x=0.1, y=0.1, w=0.8, h=0.8), z=0),
        ))
        doc = DesignDoc(pack=PackRef(id=pack.id), canvas=pack.canvas,
                        grid=pack.grid, pages=(page,))
        with pytest.raises(InvalidDesignDoc):
            render_doc(doc, pack, str(tmp_path / "out.pptx"))

    def test_a_valid_document_renders(self, pack, tmp_path):
        from app.renderer.designdoc_renderer import render_doc

        shape = shape_from("content", "A heading", LONG)
        page = build_page("p1", shape, choose(shape, pack), pack)
        doc = DesignDoc(pack=PackRef(id=pack.id), canvas=pack.canvas,
                        grid=pack.grid, pages=(page,))
        out = str(tmp_path / "out.pptx")
        render_doc(doc, pack, out)
        assert os.path.exists(out)
