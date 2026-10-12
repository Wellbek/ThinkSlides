"""
The three rungs a page can be revised on, and the cap that keeps it bounded.

Validation used to have one remedy. A page it disliked was sent back to
`draft`, which rewrote the slide from scratch - so a frame sitting two
centimetres too high cost the deck every word on that page, and the copy that
came back was different copy nobody had complained about.

The ladder is: nudge a frame, place the page again on a different arrangement,
or redraft it. The smallest that would work is preferred, the model judges
which that is, and every rung lands under the same guarantee `repair` already
gave - the page gets better or it does not change.
"""
import pytest

from app.design import run as design_run
from app.design.authored import Language, PageKind, Slot, to_pack
from app.design.repair import (
    MIN_BAND_HEIGHT, PATCHES, apply_if_better, apply_patch, score,
)
from app.design.schema import Canvas, PatchOp
from app.design.vocabulary import ensure
from app.renderer.specimen import language_specimen_slides

CANVAS = Canvas(w_in=13.333, h_in=7.5)


def _language() -> Language:
    return Language(
        name="Ladder", rationale="a probe", columns=6, body_pt=18.0,
        pages=(
            PageKind(name="cover", role="cover", frequency=0.1, slots=(
                Slot(kind="heading", span=6, required=True),
                Slot(kind="standfirst", span=5))),
            PageKind(name="page", role="content", frequency=0.9, slots=(
                Slot(kind="heading", span=6, required=True),
                Slot(kind="figure", span=3),
                Slot(kind="prose", span=3))),
        ),
    )


@pytest.fixture
def pack():
    return ensure(to_pack(_language(), CANVAS))


@pytest.fixture
def doc(pack):
    return design_run.compose(language_specimen_slides(), pack, title="probe")


def _band(page, role: str):
    return next(f for f in page.frames
                if f.role == role and f.place.kind == "grid")


class TestRungOneMovesOneFrame:
    """The operation the closed vocabulary could not express.

    Every other patch changes what a frame contains or how it is set - its
    columns, its type role, its fit, its ground, its depth. None of them could
    move it, so "this sits too high" had no expression at all and the only
    remedy left was to redraft the slide.
    """

    def test_move_band_is_in_the_vocabulary(self):
        assert "move_band" in PATCHES

    def test_a_frame_can_be_moved_down(self, doc):
        page = doc.pages[1]
        before = _band(page, "headline").place.y
        moved = apply_patch(page, PatchOp(
            op="move_band",
            args={"frame_id": _band(page, "headline").id, "dy": 0.1}))
        assert _band(moved, "headline").place.y == pytest.approx(
            before + 0.1, abs=1e-3)

    def test_a_frame_can_be_resized(self, doc):
        page = doc.pages[1]
        frame = _band(page, "headline")
        grown = apply_patch(page, PatchOp(
            op="move_band", args={"frame_id": frame.id, "dh": 0.05}))
        assert _band(grown, "headline").place.h == pytest.approx(
            frame.place.h + 0.05, abs=1e-3)

    def test_a_band_is_never_shrunk_to_nothing(self, doc):
        """`GridPlace` refuses a height of zero, so an unclamped delta is a
        patch that cannot be constructed - which is a patch that silently does
        nothing."""
        page = doc.pages[1]
        squashed = apply_patch(page, PatchOp(
            op="move_band",
            args={"frame_id": _band(page, "headline").id, "dh": -99.0}))
        assert _band(squashed, "headline").place.h >= MIN_BAND_HEIGHT

    def test_only_bands_move(self, doc):
        """A ground is placed by fraction of its parent and is not part of the
        stack."""
        page = doc.pages[0]
        grounds = [f for f in page.frames if f.place.kind == "frac"]
        if not grounds:
            pytest.skip("this page has no fraction-placed frame")
        moved = apply_patch(page, PatchOp(
            op="move_band", args={"frame_id": grounds[0].id, "dy": 0.5}))
        assert moved.frames == page.frames

    def test_an_unknown_operation_is_still_refused(self, doc):
        with pytest.raises(ValueError):
            apply_patch(doc.pages[0], PatchOp(op="teleport", args={}))


class TestEveryRungIsNeverWorse:
    """A cap bounds a loop; it does not stop it ending on its worst attempt."""

    def test_a_patch_that_helps_is_kept(self, doc, pack):
        page = doc.pages[1]
        frame = _band(page, "headline")
        # Push the page into a defect, then offer the patch that undoes it.
        broken = apply_patch(page, PatchOp(
            op="move_band", args={"frame_id": frame.id, "dy": 1.4}))
        broken_doc = doc.model_copy(update={"pages": tuple(
            broken if p.id == page.id else p for p in doc.pages)})
        assert score(broken_doc, broken, pack) > score(doc, page, pack)

        healed = apply_if_better(broken_doc, broken, pack, [PatchOp(
            op="move_band", args={"frame_id": frame.id, "dy": -1.4})])
        assert healed.applied
        assert healed.after < healed.before

    def test_a_patch_that_hurts_is_discarded(self, doc, pack):
        page = doc.pages[1]
        result = apply_if_better(doc, page, pack, [PatchOp(
            op="move_band",
            args={"frame_id": _band(page, "headline").id, "dy": 1.6})])
        assert result.page == page
        assert result.applied == ()

    def test_a_patch_naming_nothing_changes_nothing(self, doc, pack):
        result = apply_if_better(doc, doc.pages[1], pack, [PatchOp(
            op="move_band", args={"frame_id": "no-such-frame", "dy": 0.2})])
        assert result.page.frames == doc.pages[1].frames


class TestRungTwoPlacesThePageAgain:
    """Same material, different arrangement. `choose` has always accepted a
    candidate list; nothing passed it one."""

    def test_avoiding_an_archetype_picks_a_different_one(self, pack):
        first = design_run.compose(language_specimen_slides(), pack)
        used = first.pages[1].archetype
        again = design_run.compose(
            language_specimen_slides(), pack, avoid={1: frozenset({used})})
        assert again.pages[1].archetype != used

    def test_the_copy_is_untouched_by_a_re_placement(self, pack):
        def words(doc, index):
            out = []
            for frame in doc.pages[index].frames:
                for item in frame.content:
                    out.extend(getattr(item, "runs", []) or [])
            return sorted(str(getattr(r, "text", r)) for r in out)

        first = design_run.compose(language_specimen_slides(), pack)
        again = design_run.compose(
            language_specimen_slides(), pack,
            avoid={1: frozenset({first.pages[1].archetype})})
        assert words(first, 1) == words(again, 1)

    def test_avoiding_everything_places_the_page_anyway(self, pack):
        """Refusing to place a page at all is a worse outcome than placing it
        the way we already did."""
        everything = frozenset(a.id for a in pack.archetypes)
        doc = design_run.compose(
            language_specimen_slides(), pack,
            avoid={i: everything for i in range(3)})
        assert len(doc.pages) == 3


class TestTheVerdictNamesTheRung:
    def test_the_tiers_are_ordered_smallest_first(self):
        from app.agent.tools.validate_rendered_output import (
            FIX_NUDGE, FIX_REDRAFT, FIX_REPLACE, FIX_TIERS,
        )

        assert FIX_TIERS == (FIX_NUDGE, FIX_REPLACE, FIX_REDRAFT)

    def test_an_unreadable_verdict_costs_the_cheapest_rung(self):
        """Defaulting down: a verdict nobody can parse should cost a nudge the
        ratchet will discard, not a redraft that throws away copy nobody
        complained about."""
        from app.agent.tools.validate_rendered_output import (
            FIX_NUDGE, _fix_tier,
        )

        for raw in (None, "", "something else", 7):
            assert _fix_tier(raw) == FIX_NUDGE

    def test_a_named_rung_is_honoured(self):
        from app.agent.tools.validate_rendered_output import _fix_tier

        assert _fix_tier("replace") == "replace"
        assert _fix_tier(" REDRAFT ") == "redraft"

    def test_the_prompt_asks_about_arrangement_and_rules_out_taste(self):
        """The old wording ruled out "subjective style preferences", which was
        the right instinct and too broad a cut - it excluded the whole class of
        defect an authored design language actually produces."""
        from app.agent.tools.validate_rendered_output import _aesthetic_prompt

        prompt = _aesthetic_prompt().lower()
        assert "empty" in prompt and "crowded" in prompt
        assert "colours" in prompt and "typeface" in prompt
        for tier in ("nudge", "replace", "redraft"):
            assert tier in prompt


class TestThePageIsRevisedAtMostTwice:
    """Per slide, not per run. A run-level cap lets one stubborn page spend the
    whole budget and leaves every other flagged page unfixed."""

    @staticmethod
    def _state():
        from app.agent.loop import WorkingState

        return WorkingState()

    def test_a_flagged_page_is_offered_its_rung(self):
        state = self._state()
        wanted = state.layout_revision_for([
            {"slideIndex": 2, "type": "aesthetic", "fix": "nudge"}])
        # Answered as a replace: a nudge re-rendered the identical page (#359).
        assert wanted == {2: "replace"}

    def test_a_page_stops_being_offered_at_the_cap(self):
        state = self._state()
        issues = [{"slideIndex": 2, "type": "aesthetic", "fix": "replace"}]
        for _ in range(state.LAYOUT_REVISION_CAP):
            assert state.layout_revision_for(issues) == {2: "replace"}
            state.record_layout_revision(2, "page")
        assert state.layout_revision_for(issues) == {}

    def test_a_capped_page_is_not_quietly_promoted_to_a_redraft(self):
        """The point of the cap is that the run stops."""
        state = self._state()
        for _ in range(state.LAYOUT_REVISION_CAP):
            state.record_layout_revision(4, "page")
        assert state.layout_revision_for(
            [{"slideIndex": 4, "fix": "nudge"}]) == {}

    def test_the_cap_is_counted_per_page(self):
        state = self._state()
        for _ in range(state.LAYOUT_REVISION_CAP):
            state.record_layout_revision(1, "page")
        wanted = state.layout_revision_for([
            {"slideIndex": 1, "fix": "nudge"},
            {"slideIndex": 2, "fix": "nudge"}])
        assert wanted == {2: "replace"}

    def test_the_larger_rung_wins_when_a_page_draws_both(self):
        state = self._state()
        wanted = state.layout_revision_for([
            {"slideIndex": 0, "fix": "nudge"},
            {"slideIndex": 0, "fix": "replace"}])
        assert wanted == {0: "replace"}

    def test_a_redraft_verdict_is_not_a_layout_revision(self):
        """Redrafting is the third rung and goes through the path that already
        existed - `slides_needing_revision` - not through this one."""
        state = self._state()
        assert state.layout_revision_for(
            [{"slideIndex": 0, "fix": "redraft"}]) == {}

    def test_what_a_page_has_been_placed_on_is_not_offered_again(self):
        state = self._state()
        state.record_layout_revision(3, "page")
        state.record_layout_revision(3, "page-stacked")
        assert state.arrangements_to_avoid() == {
            3: frozenset({"page", "page-stacked"})}


class TestTheCapSurvivesAPause:
    """Without this the cap is two *per reload*, and the run's state is
    written to Redis and read back on every resume."""

    @pytest.mark.asyncio
    async def test_a_resumed_run_remembers_what_it_tried(self):
        import uuid

        from tests.test_agent_loop_workflow import _FakeRedis, _loop

        session_id = uuid.uuid4()
        loop = _loop()
        loop.redis_client = _FakeRedis()
        loop.working_state.record_layout_revision(1, "page")
        loop.working_state.record_layout_revision(1, "page-stacked")
        loop.working_state.record_layout_revision(4, "other")
        loop.working_state.placed_arrangements = {1: ["page-wide"]}
        await loop._save_working_state(session_id)

        resumed = _loop()
        resumed.redis_client = loop.redis_client
        await resumed._load_working_state(session_id)

        assert resumed.working_state.layout_revisions == {1: 2, 4: 1}
        assert resumed.working_state.arrangements_to_avoid() == {
            1: frozenset({"page", "page-stacked"}),
            4: frozenset({"other"}),
        }
        assert resumed.working_state.placed_arrangements == {1: ["page-wide"]}

    @pytest.mark.asyncio
    async def test_a_run_with_nothing_to_remember_resumes_clean(self):
        import uuid

        from tests.test_agent_loop_workflow import _FakeRedis, _loop

        session_id = uuid.uuid4()
        loop = _loop()
        loop.redis_client = _FakeRedis()
        await loop._save_working_state(session_id)

        resumed = _loop()
        resumed.redis_client = loop.redis_client
        await resumed._load_working_state(session_id)

        assert resumed.working_state.layout_revisions == {}
        assert resumed.working_state.arrangements_to_avoid() == {}


class TestTheLadderClosesTheLoop:
    """The three pieces wired together: a verdict names a rung, the loop
    records it, and the next render composes differently."""

    def test_a_replace_verdict_rules_out_what_was_placed(self, pack):
        from app.agent.tools.finalize_and_render_pptx import (
            FinalizeAndRenderPptxTool,
        )
        from tests.test_agent_loop_workflow import _loop

        # What a first render would have placed.
        first = design_run.compose(language_specimen_slides(), pack)
        placed: dict[int, list[str]] = {}
        for page in first.pages:
            source = page.provenance.source_slide
            if source is not None:
                placed.setdefault(source, []).append(page.archetype)
        assert placed, "a composed page must say which slide it came from"

        loop = _loop()
        loop.working_state.placed_arrangements = placed
        wanted = loop.working_state.layout_revision_for([
            {"slideIndex": 1, "type": "aesthetic", "fix": "replace"}])
        assert wanted == {1: "replace"}

        for index in wanted:
            loop.working_state.record_layout_revision(
                index, (loop.working_state.placed_arrangements.get(index)
                        or [""])[0])

        avoid = loop.working_state.arrangements_to_avoid()
        assert avoid[1] == frozenset({placed[1][0]})

        again = design_run.compose(language_specimen_slides(), pack,
                                   avoid=avoid)
        assert again.pages[1].archetype != first.pages[1].archetype
        assert FinalizeAndRenderPptxTool is not None

    def test_what_was_placed_crosses_the_task_the_render_runs_in(
            self, pack, tmp_path, monkeypatch):
        """The loop awaits every tool as its own task, which runs in a copy of
        the context. The renderer used to leave what it placed in a context
        variable, set inside that copy and never seen by the loop, so every
        "replace" verdict ruled out nothing and the page came back on the
        arrangement it was flagged for (#344). This runs the real tool across
        that same boundary."""
        import asyncio
        import json

        from app.agent import run_context
        from app.agent.tools import finalize_and_render_pptx as finalize
        from tests.test_agent_loop_workflow import _loop

        monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
        monkeypatch.setattr(finalize.PreviewGenerator, "generate_previews",
                            lambda self, path, session_id: [])
        loop = _loop()

        async def render():
            token = run_context.set_pack(pack)
            try:
                return await loop._with_retry_notices(
                    finalize.FinalizeAndRenderPptxTool.run(
                        slides_json=json.dumps(language_specimen_slides()),
                        session_id="probe"),
                    phase="render",
                ).__anext__()
            finally:
                run_context.reset_pack(token)

        kind, result = asyncio.run(render())
        assert kind == "result" and result.success, result.error

        assert result.data["renderPath"] == "composed"
        placed = result.data["placedArrangements"]
        first = design_run.compose(language_specimen_slides(), pack)
        assert placed[1] == [p.archetype for p in first.pages
                             if p.provenance.source_slide == 1]

    def test_a_nudge_verdict_rules_the_arrangement_out(self):
        """Nothing in composition reads a nudge, so keeping the arrangement
        re-rendered the identical page (#359). It is answered as a replace."""
        from tests.test_agent_loop_workflow import _loop

        loop = _loop()
        wanted = loop.working_state.layout_revision_for(
            [{"slideIndex": 1, "fix": "nudge"}])
        assert wanted == {1: "replace"}
        loop.working_state.record_layout_revision(1, "page")
        assert loop.working_state.arrangements_to_avoid() == {1: {"page"}}

    def test_a_layout_verdict_does_not_redraft_the_slide(self, monkeypatch):
        """Redrafting a slide to move a frame down throws away copy nobody
        complained about, and brings back different copy - which is how a page
        with one thing wrong comes back with three."""
        from tests.test_agent_loop_workflow import _loop

        loop = _loop()
        issues = [{"slideIndex": 1, "type": "aesthetic", "fix": "replace"}]
        relayout = loop.working_state.layout_revision_for(issues)
        remaining = [i for i in issues
                     if i.get("slideIndex") not in relayout]
        assert loop.working_state.compute_slides_needing_revision(
            remaining) == []

    def test_a_redraft_verdict_still_redrafts(self):
        from tests.test_agent_loop_workflow import _loop

        loop = _loop()
        loop.working_state.accumulated_slides = [
            {"slideIndex": i} for i in range(6)]
        issues = [{"slideIndex": 1, "type": "aesthetic", "fix": "redraft"}]
        relayout = loop.working_state.layout_revision_for(issues)
        remaining = [i for i in issues
                     if i.get("slideIndex") not in relayout]
        assert relayout == {}
        assert loop.working_state.compute_slides_needing_revision(
            remaining) == [1]


class TestARowClosesUpWhenItLosesAMember:
    """The horizontal half of the gap-closing problem.

    `_compact` closed the gap a dropped frame left *above* the frames below
    it. Once slots could share a row, a frame could also lose the thing beside
    it - and an archetype of `heading 6 / figure 4 / prose 2`, used for a page
    with no chart, put its only paragraph in a two-column strip at the right
    edge with four empty columns to its left. The stack was clean and the page
    was broken.
    """

    @staticmethod
    def _pack():
        return ensure(to_pack(Language(
            name="Evidence", rationale="a probe", columns=6, body_pt=18.0,
            pages=(PageKind(name="evidence", role="content", frequency=1.0,
                            slots=(Slot(kind="heading", span=6, required=True),
                                   Slot(kind="figure", span=4),
                                   Slot(kind="prose", span=2))),),
        ), CANVAS))

    @staticmethod
    def _text_only():
        return [{"slideIndex": 0, "role": "content", "heading": "A heading",
                 "contents": [{"type": "text", "runs": [
                     {"text": "One point that runs a reasonable length."},
                     {"text": "A second point of about the same size."}]}]}]

    def test_a_lone_survivor_does_not_sit_at_the_right_edge(self):
        pack = self._pack()
        doc = design_run.compose(self._text_only(), pack,
                                 skip_unplaceable=True)
        prose = [f for f in doc.pages[0].frames
                 if f.role == "column" and f.place.kind == "grid"]
        assert prose and all(f.place.col == 0 for f in prose)

    def test_a_lone_survivor_takes_the_width_the_row_had(self):
        """A narrow column alone on a wide page is the same unfinished look
        the vertical compaction exists to prevent."""
        pack = self._pack()
        doc = design_run.compose(self._text_only(), pack,
                                 skip_unplaceable=True)
        prose = next(f for f in doc.pages[0].frames if f.role == "column")
        assert prose.place.span == 6

    def test_two_survivors_of_three_share_the_row(self):
        """#357: a row of three prose columns that lost one packed the other
        two to the left at a third of the page each, or - when the survivors
        sat side by side - was not closed up at all, since two frames sharing
        a band read as an overlap."""
        pack = ensure(to_pack(Language(
            name="Three", rationale="a probe", columns=6, body_pt=18.0,
            pages=(PageKind(name="three", role="content", frequency=1.0,
                            slots=(Slot(kind="heading", span=6, required=True),
                                   Slot(kind="figure", span=6),
                                   Slot(kind="prose", span=2),
                                   Slot(kind="prose", span=2),
                                   Slot(kind="prose", span=2))),),
        ), CANVAS))
        from app.design.bind import build_page
        from app.design.material import shape_from
        from app.design.select import fit

        shape = shape_from("content", "A heading", [
            "One point that runs a reasonable length.",
            "A second point of about the same size."])
        selection = fit(shape, pack.archetype("three"), pack)
        assert selection is not None
        page = build_page("p1", shape, selection, pack)
        prose = sorted((f for f in page.frames if f.role == "column"),
                       key=lambda f: f.place.col)
        assert len(prose) == 2
        assert [f.place.col for f in prose] == [0, prose[0].place.span]
        assert sum(f.place.span for f in prose) == 6

    def test_shares_always_fill_the_row_exactly(self):
        from app.design.bind import _shares

        for weights, width in (([1, 1, 1, 1], 6), ([2, 2], 6), ([3, 1, 1], 5),
                               ([1, 1, 1], 12)):
            spans = _shares(weights, width)
            assert sum(spans) == width and min(spans) >= 1, (weights, width)
        assert _shares([1, 1, 1], 2) is None

    def test_frames_stacked_in_one_band_are_not_spread_across_it(self):
        """A tall figure beside a heading stacked over prose shares a height
        band with both; spreading them across the band set the heading and
        the prose beside each other."""
        from app.design.bind import _closed_up
        from app.design.schema import Frame, GridPlace

        def frame(name, col, span, y, h):
            return Frame(id=name, role="column",
                         place=GridPlace(col=col, span=span, y=y, h=h))

        original = (frame("figure", 0, 6, 0.2, 0.7), frame("heading", 6, 6, 0.2, 0.1),
                    frame("chart", 6, 6, 0.35, 0.2), frame("prose", 6, 6, 0.6, 0.3))
        kept = [original[0], original[1], original[3]]
        assert _closed_up([kept], original) == {}

    def test_a_row_that_kept_everything_is_untouched(self):
        """A page that lost nothing is composed exactly as its archetype
        states, which is what a measured pack is for."""
        pack = self._pack()
        archetype = pack.archetype("evidence")
        before = {f.id: (f.place.col, f.place.span) for f in archetype.frames
                  if f.place.kind == "grid"}
        doc = design_run.compose(language_specimen_slides()[2:], pack,
                                 skip_unplaceable=True)
        page = doc.pages[0]
        if page.archetype != "evidence":
            pytest.skip("this material chose a different arrangement")
        kept = {f.id for f in page.frames}
        if kept != set(before):
            pytest.skip("this page dropped a frame")
        for frame in page.frames:
            if frame.place.kind == "grid":
                assert (frame.place.col, frame.place.span) == before[frame.id]

    def test_the_page_is_still_geometrically_sound(self):
        from app.design.geometry import check_doc

        doc = design_run.compose(self._text_only(), self._pack(),
                                 skip_unplaceable=True)
        assert check_doc(doc) == []


class TestAPageThatCannotBeMoved:
    """Probes (#376) saw `replace` hand back the page it was asked to move:
    composition ignores an avoid set that would leave no arrangement, so the
    verdict spent a revision on an identical page."""

    def test_an_exhausted_page_is_not_offered_a_replace_again(self):
        from tests.test_agent_loop_workflow import _loop

        state = _loop().working_state
        issues = [{"slideIndex": 2, "type": "aesthetic", "fix": "replace"}]
        assert state.layout_revision_for(issues) == {2: "replace"}
        state.layout_exhausted.add(2)
        # Left out, so the finding goes on to the redraft rung.
        assert state.layout_revision_for(issues) == {}
        assert state.compute_slides_needing_revision(issues) == [2]

    def test_exhaustion_survives_a_pause(self):
        from tests.test_agent_loop_workflow import _loop

        loop = _loop()
        loop.working_state.layout_exhausted = {1, 3}
        saved = loop._state_dict()
        assert saved["layout_exhausted"] == [1, 3]
        other = _loop()
        assert other._apply_state_dict(saved, "sid")
        assert other.working_state.layout_exhausted == {1, 3}


class TestARenderSaysWhichPagesCannotMove:
    """Probes (#376) saw a second `replace` swap two pages between the only
    two arrangements that held them, and a third hand both back unchanged.
    Composition knows what else could carry a page, so the loop need not
    spend a render to find out."""

    def test_a_page_names_the_other_arrangements_that_could_carry_it(
            self, pack):
        doc = design_run.compose(language_specimen_slides(), pack)
        page = doc.pages[1]
        assert page.provenance.alternatives
        assert page.archetype not in page.provenance.alternatives
        again = design_run.compose(
            language_specimen_slides(), pack,
            avoid={1: frozenset({page.provenance.alternatives[0]})})
        assert (page.provenance.alternatives[0]
                not in again.pages[1].provenance.alternatives)

    def test_a_page_with_every_other_arrangement_tried_has_none_left(
            self, pack):
        first = design_run.compose(language_specimen_slides(), pack)
        others = frozenset(a.id for a in pack.archetypes) - {
            first.pages[1].archetype}
        again = design_run.compose(language_specimen_slides(), pack,
                                   avoid={1: others})
        assert again.pages[1].archetype == first.pages[1].archetype
        assert again.pages[1].provenance.alternatives == ()

    def test_the_render_reports_the_slides_it_cannot_move(self, pack, tmp_path):
        from app.agent import run_context
        from app.agent.tools.finalize_and_render_pptx import (
            FinalizeAndRenderPptxTool,
        )

        avoid = {1: frozenset(a.id for a in pack.archetypes)}
        token = run_context.set_arrangements_to_avoid(avoid)
        try:
            unmovable = FinalizeAndRenderPptxTool._render_composed(
                language_specimen_slides(), pack, {"title": "Deck"},
                str(tmp_path / "deck.pptx"), {}).unmovable
        finally:
            run_context.reset_arrangements_to_avoid(token)
        doc = design_run.compose(language_specimen_slides(), pack, avoid=avoid)
        movable = {p.provenance.source_slide for p in doc.pages
                   if p.provenance.alternatives}
        assert 1 in unmovable
        assert unmovable == sorted(
            {p.provenance.source_slide for p in doc.pages} - movable)
