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


class TestAnAmbiguousOverlapCarriesItsOwnFix:
    """The renderer refuses a document whose frames overlap at the same depth,
    so a finding without a patch is a page the run cannot ship. That is not
    hypothetical: a six-slide deck composed seven pages and rendered none of
    them, because the packs are traced from real pages and some traces put two
    content frames on top of each other."""

    def _overlapping(self, pack):
        page = Page(id="p", archetype="test", frames=(
            Frame(id="a", role="column",
                  place=FracPlace(x=0.1, y=0.1, w=0.5, h=0.5), z=2,
                  content=(TextBlock(type_role="body", paragraphs=(
                      Paragraph(runs=(Run(text="One."),)),
                  )),)),
            Frame(id="b", role="column",
                  place=FracPlace(x=0.3, y=0.3, w=0.5, h=0.5), z=2,
                  content=(TextBlock(type_role="body", paragraphs=(
                      Paragraph(runs=(Run(text="Two."),)),
                  )),)),
        ))
        doc = DesignDoc(pack=PackRef(id=pack.id), canvas=pack.canvas,
                        grid=pack.grid, pages=(page,))
        return doc, page

    def test_the_overlap_is_reported(self, pack):
        doc, page = self._overlapping(pack)
        assert any(f.property == "geometry" for f in diagnose(doc, page, pack))

    def test_the_finding_knows_what_to_do_about_it(self, pack):
        doc, page = self._overlapping(pack)
        finding = next(f for f in diagnose(doc, page, pack)
                       if f.property == "geometry")
        assert finding.patch is not None
        assert finding.patch.op == "set_z"

    def test_repairing_it_makes_the_page_placeable(self, pack):
        from app.design.geometry import check_page

        doc, page = self._overlapping(pack)
        assert check_page(doc, page)
        result = repair(doc, page, pack)
        repaired_doc = doc.model_copy(update={"pages": (result.page,)})
        assert check_page(repaired_doc, result.page) == []

    def test_the_later_frame_is_the_one_lifted(self, pack):
        """Either resolves the ambiguity; lifting the later one is the
        convention that matches how the frames were declared."""
        doc, page = self._overlapping(pack)
        result = repair(doc, page, pack)
        assert result.page.frame_by_id("b").z > result.page.frame_by_id("a").z

    def test_a_frame_escaping_the_page_gets_no_invented_fix(self, pack):
        """A different fault with a different answer. A patch here would be
        the repair loop pretending to fix something."""
        page = Page(id="p", archetype="test", frames=(
            Frame(id="gone", role="column",
                  place=FracPlace(x=1.4, y=1.4, w=0.5, h=0.5)),
        ))
        doc = DesignDoc(pack=PackRef(id=pack.id), canvas=pack.canvas,
                        grid=pack.grid, pages=(page,))
        findings = [f for f in diagnose(doc, page, pack)
                    if f.property == "geometry"]
        assert findings
        assert all(f.patch is None for f in findings)


class TestOverflowHasAMoveThatActuallyHelps:
    """`set_columns` was the only answer to overset copy, and it is usually
    the wrong one: a narrower measure produces disproportionately more lines,
    so splitting a 500pt column in three takes a block from 360pt deep to
    405pt. The gate then rejected it and the page shipped with its copy
    running over the block beneath - text overlapping text in a rendered
    deck, on a page that passed geometry validation."""

    def test_the_patch_steps_down_the_pack_s_own_ramp(self, pack):
        from app.design.repair import _next_smaller_role

        assert _next_smaller_role(pack, "headline") is not None
        smaller = _next_smaller_role(pack, "body")
        if smaller is not None:
            assert pack.role(smaller).size_pt < pack.role("body").size_pt

    def test_the_smallest_rung_has_nowhere_to_go(self, pack):
        from app.design.repair import _next_smaller_role

        smallest = min(pack.type_roles, key=lambda n: pack.role(n).size_pt)
        assert _next_smaller_role(pack, smallest) is None

    def test_an_overset_block_is_offered_a_smaller_setting(self, pack):
        doc, page = _overset_doc(pack)
        finding = next(f for f in diagnose(doc, page, pack)
                       if f.property == "overflow")
        assert finding.patch is not None
        assert finding.patch.op in ("set_type_role", "set_columns")

    def test_setting_a_frame_smaller_changes_only_that_frame(self, pack):
        doc, page = _overset_doc(pack)
        smaller = min(pack.type_roles, key=lambda n: pack.role(n).size_pt)
        result = apply_patch(page, PatchOp(
            op="set_type_role",
            args={"frame_id": "tiny", "type_role": smaller},
        ))
        block = result.frame_by_id("tiny").content[0]
        assert block.type_role == smaller
        assert page.frame_by_id("tiny").content[0].type_role == "body"

    def test_the_gate_still_refuses_a_step_that_costs_more_than_it_saves(
            self, pack):
        """Stepping below the pack's legibility floor raises a legibility
        finding, which raises the score, which the monotone gate refuses. The
        mechanism that keeps a repair honest keeps it legible for free."""
        doc, page = _overset_doc(pack)
        result = repair(doc, page, pack)
        assert result.after <= result.before

    def test_a_real_page_that_oversets_is_repaired_where_it_can_be(self, pack):
        from app.design.run import compose
        from app.design.repair import diagnose as _diagnose

        slides = [{"slideIndex": 0, "role": "content", "contents": [
            {"type": "text", "runs": [{"text": "Cyber risks in 2026"}]},
            {"type": "text", "runs": [{"text": line} for line in LONG]},
        ]}]
        composed = compose(slides, pack)
        for page in composed.pages:
            findings = _diagnose(composed, page, pack)
            # Either the page is clean, or the loop tried and the gate held.
            assert all(f.severity <= 5 for f in findings)
