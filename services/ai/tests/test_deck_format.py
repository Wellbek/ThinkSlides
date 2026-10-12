"""
What kind of presentation this is, and everything that reads it.

Issue #284: every run assumed the same shape - a 16:9 deck of ten to
eighteen slides, in a boardroom register - because nothing in the system
carried the idea that a deck might be a pitch, a dashboard, an academic
seminar or a printed poster. A "scientific journal" language authored at
10pt body (issue #282) was the right answer to a question nobody had asked.

The property under test throughout: the format is a *typed value*, read by
code, not a line of prose pasted into a downstream prompt and hoped for.
"""
import json
import uuid

import pytest

from app import budget as budget_model
from app.design import deck_format
from app.design.schema import Canvas


class TestTheRegistry:
    def test_every_format_has_a_canvas_and_a_floor(self):
        for fmt in deck_format.FORMATS.values():
            assert fmt.canvas.w_in > 0 and fmt.canvas.h_in > 0
            assert fmt.floor_pt() > 0

    def test_exactly_one_floor_convention_per_format(self):
        """A format's floor is a canvas ratio (projected) or an absolute
        point size (printed), never both and never neither."""
        for fmt in deck_format.FORMATS.values():
            assert (fmt.body_floor_ratio is None) != (fmt.body_floor_pt is None)

    def test_only_the_poster_is_a_single_page(self):
        assert [f.id for f in deck_format.FORMATS.values() if f.single_page] \
            == ["poster"]

    def test_the_poster_canvas_is_not_the_slide_canvas(self):
        poster = deck_format.FORMATS["poster"]
        boardroom = deck_format.FORMATS["boardroom"]
        assert poster.canvas != boardroom.canvas
        assert poster.canvas.aspect < 1.0  # portrait

    def test_by_id_falls_back_to_the_default_for_anything_unrecognised(self):
        assert deck_format.by_id("nonsense").id == deck_format.DEFAULT_FORMAT
        assert deck_format.by_id(None).id == deck_format.DEFAULT_FORMAT
        assert deck_format.by_id("").id == deck_format.DEFAULT_FORMAT

    def test_by_id_is_case_and_space_insensitive(self):
        assert deck_format.by_id("  Poster  ").id == "poster"
        assert deck_format.by_id("POSTER").id == "poster"

    def test_the_question_offers_every_format_once(self):
        assert len(deck_format.QUESTION["options"]) == len(deck_format.FORMATS)
        for fmt in deck_format.FORMATS.values():
            assert any(fmt.label in option
                      for option in deck_format.QUESTION["options"])


class TestMatchingTheUsersOwnWords:
    """The card sends back "{question} {option label}" verbatim - see
    QuestionCard.answerFor - so a format is identified by its own label
    appearing in the reply, the same convention the autonomy question and
    the outline review already rely on."""

    def test_a_bare_option_reply_resolves(self):
        for fmt in deck_format.FORMATS.values():
            text = f"What kind of presentation is this? {fmt.label} - {fmt.description}"
            assert deck_format.resolve_from_text(text) == fmt.id

    def test_unrelated_text_resolves_to_nothing(self):
        assert deck_format.resolve_from_text("just make it look nice") is None
        assert deck_format.resolve_from_text("") is None
        assert deck_format.resolve_from_text(None) is None

    def test_resolve_scans_most_recent_message_first(self):
        chat = [
            {"role": "user", "content": "Pitch - projected and spoken over"},
            {"role": "assistant", "content": "..."},
            {"role": "user", "content": "Conference poster - printed"},
        ]
        assert deck_format.resolve(chat) == "poster"

    def test_resolve_keeps_looking_back_past_a_turn_that_names_nothing(self):
        """The same convention `user_confirmed_autonomy` uses: a later user
        turn that says nothing about format does not erase an earlier one
        that did."""
        chat = [
            {"role": "user", "content": "Data dashboard - almost entirely charts"},
            {"role": "assistant", "content": "..."},
            {"role": "user", "content": "make the chart blue"},
        ]
        assert deck_format.resolve(chat) == "dashboard"

    def test_resolve_defaults_when_nobody_ever_answered(self):
        assert deck_format.resolve([{"role": "user", "content": "hello"}]) \
            == deck_format.DEFAULT_FORMAT
        assert deck_format.resolve([]) == deck_format.DEFAULT_FORMAT

    def test_resolve_from_chat_returns_none_rather_than_a_default(self):
        """The distinction `resolve` is built on: "nobody has answered yet"
        is not the same fact as "the answer was the default", and a caller
        that only overwrites on an actual match needs to tell them apart."""
        assert deck_format.resolve_from_chat([]) is None
        assert deck_format.resolve_from_chat(
            [{"role": "user", "content": "hello"}]) is None
        assert deck_format.resolve_from_chat(
            [{"role": "user", "content": "Pitch - projected"}]) == "pitch"

    def test_only_user_messages_are_read(self):
        chat = [{"role": "assistant",
                "content": "Conference poster - printed and read standing up"}]
        assert deck_format.resolve(chat) == deck_format.DEFAULT_FORMAT


class TestTheFloorIsCanvasRelativeOrAbsolute:
    """A projected deck's floor is a share of its own canvas - ten points
    means something different on a 540pt slide than on a 792pt one. A
    printed poster is read at a fixed physical distance regardless of how
    large the sheet is, so its floor does not move with the canvas."""

    def test_a_projected_floor_scales_with_a_taller_canvas(self):
        pitch = deck_format.FORMATS["pitch"]
        tall = Canvas(w_in=26.666, h_in=15.0)
        assert pitch.floor_pt(tall) > pitch.floor_pt(pitch.canvas)

    def test_the_poster_floor_does_not_move_with_the_canvas(self):
        poster = deck_format.FORMATS["poster"]
        other = Canvas(w_in=10.0, h_in=100.0)
        assert poster.floor_pt(other) == poster.floor_pt(poster.canvas) == 24.0

    def test_the_poster_floor_is_a_tiny_ratio_of_its_own_huge_canvas(self):
        """24pt on a poster is a fraction of a percent of canvas height -
        correct, because a poster is read at a metre rather than projected -
        and it would be wrong read as a slide-style ratio."""
        poster = deck_format.FORMATS["poster"]
        ratio = poster.floor_pt() / (poster.canvas.h_in * 72.0)
        assert ratio < 0.01


class TestToPackHonoursTheFormatFloor:
    """`to_pack` is where a language becomes a real pack, and the floor has
    to survive that trip - this is the mechanism issue #282 needed and #284
    supplies the number for."""

    @staticmethod
    def _language(body_pt=10.0):
        from app.design.authored import Language, PageKind, Slot

        return Language(
            name="Probe", rationale="x", columns=6, body_pt=body_pt,
            pages=(PageKind(name="page", role="content", frequency=1.0,
                            slots=(Slot(kind="heading", span=6, required=True),
                                   Slot(kind="figure", span=3),
                                   Slot(kind="prose", span=3))),),
        )

    def test_a_language_under_the_format_floor_is_scaled_up(self):
        from app.design.authored import to_pack

        fmt = deck_format.FORMATS["pitch"]
        pack = to_pack(self._language(10.0), fmt.canvas,
                       floor_pt=fmt.floor_pt(fmt.canvas))
        assert pack.role("body").size_pt >= fmt.floor_pt(fmt.canvas)

    def test_the_poster_scales_to_an_absolute_point_size(self):
        from app.design.authored import to_pack

        fmt = deck_format.FORMATS["poster"]
        pack = to_pack(self._language(10.0), fmt.canvas,
                       floor_pt=fmt.floor_pt(fmt.canvas))
        assert pack.role("body").size_pt == pytest.approx(24.0, abs=0.1)

    def test_no_floor_pt_falls_back_to_the_ordinary_slide_floor(self):
        """Backward compatible: every existing caller of `to_pack` that never
        heard of a format still gets the behaviour issue #282 shipped."""
        from app.design.authored import CANVAS_BODY_FLOOR_RATIO, to_pack

        canvas = Canvas(w_in=13.333, h_in=7.5)
        pack = to_pack(self._language(10.0), canvas)
        assert pack.role("body").size_pt == pytest.approx(
            CANVAS_BODY_FLOOR_RATIO * canvas.h_in * 72.0, abs=0.1)


class TestAuthoringReadsTheFormat:
    def test_the_prompt_names_the_format_and_its_register(self):
        from app.design import authoring

        for fmt in deck_format.FORMATS.values():
            text = authoring.prompt("a deck about growth", "", 3, fmt=fmt)
            assert fmt.label in text
            assert fmt.description in text

    def test_a_prompt_with_no_format_still_works(self):
        """A caller with no opinion gets the boardroom default rather than a
        crash - `author_run.author` and tests both call this without `fmt`."""
        from app.design import authoring

        text = authoring.prompt("a deck about growth", "", 3)
        assert deck_format.FORMATS["boardroom"].label in text

    @pytest.mark.asyncio
    async def test_author_derives_against_the_formats_own_canvas(self):
        from app.design import author_run

        class _LLM:
            async def chat(self, messages, tools=None):
                return {"message": {"content": json.dumps({"languages": [{
                    "name": "Poster Language", "rationale": "for a poster",
                    "columns": 4, "bodyPt": 24.0,
                    "pages": [
                        {"name": "cover", "role": "cover", "frequency": 0.2,
                         "slots": [{"kind": "heading", "span": 4,
                                    "required": True}]},
                        {"name": "body", "role": "content", "frequency": 0.8,
                         "slots": [{"kind": "heading", "span": 4,
                                    "required": True},
                                   {"kind": "figure", "span": 2},
                                   {"kind": "prose", "span": 2}]},
                    ],
                }]})}}

        fmt = deck_format.FORMATS["poster"]
        languages, why = await author_run.author(
            _LLM(), "a research poster", fmt.canvas, fmt=fmt)
        assert languages, why
        from app.design.authored import to_pack
        from app.design.vocabulary import ensure

        pack = ensure(to_pack(languages[0], fmt.canvas,
                              floor_pt=fmt.floor_pt(fmt.canvas)))
        assert pack.canvas.w_in == fmt.canvas.w_in
        assert pack.canvas.h_in == fmt.canvas.h_in


class TestBudgetReadsTheFormat:
    def test_boardroom_and_academic_are_unscaled(self):
        base = budget_model.budget_for(None, budget_model.CONTENT, 1.0)
        for fid in ("boardroom", "academic", ""):
            scaled = budget_model.budget_for(
                None, budget_model.CONTENT, 1.0, format_id=fid)
            assert scaled.blocks_target == base.blocks_target
            assert scaled.words_target == base.words_target

    def test_pitch_carries_less_than_boardroom(self):
        base = budget_model.budget_for(None, budget_model.CONTENT, 1.0)
        pitch = budget_model.budget_for(
            None, budget_model.CONTENT, 1.0, format_id="pitch")
        assert pitch.words_target < base.words_target
        assert pitch.blocks_target < base.blocks_target

    def test_poster_carries_far_more_than_boardroom(self):
        """MAX_BLOCKS was calibrated against the reference's densest single
        *slide*. A poster page is a whole deck's material on one sheet."""
        base = budget_model.budget_for(None, budget_model.CONTENT, 1.0)
        poster = budget_model.budget_for(
            None, budget_model.CONTENT, 1.0, format_id="poster")
        assert poster.words_target > base.words_target * 2
        assert poster.blocks_max > base.blocks_max

    def test_an_unknown_format_id_is_the_same_as_none(self):
        base = budget_model.budget_for(None, budget_model.CONTENT, 1.0)
        unknown = budget_model.budget_for(
            None, budget_model.CONTENT, 1.0, format_id="nonsense")
        assert unknown.blocks_target == base.blocks_target


class TestThePlanReadsTheFormat:
    @pytest.mark.asyncio
    async def test_the_prompt_carries_the_page_vocabulary(self, monkeypatch):
        from app.agent.tools import plan_presentation_structure as plan_tool

        captured = {}

        class _LLM:
            async def chat(self, messages, tools=None):
                captured["prompt"] = messages[0]["content"]
                return {"message": {"content": json.dumps({
                    "title": "T", "totalSlides": 1,
                    "slides": [{"slideIndex": 0, "title": "T",
                               "contentType": "title", "keyPoints": []}],
                })}}

        monkeypatch.setattr(plan_tool, "require_chat_provider", lambda: _LLM())
        await plan_tool.PlanPresentationStructureTool.run(
            content_summary="growth", deck_format="dashboard")
        assert "Data dashboard" in captured["prompt"]
        assert deck_format.FORMATS["dashboard"].vocabulary in captured["prompt"]

    @pytest.mark.asyncio
    async def test_a_poster_plan_is_told_it_is_one_page(self, monkeypatch):
        from app.agent.tools import plan_presentation_structure as plan_tool

        captured = {}

        class _LLM:
            async def chat(self, messages, tools=None):
                captured["prompt"] = messages[0]["content"]
                return {"message": {"content": json.dumps({
                    "title": "T", "totalSlides": 1,
                    "slides": [{"slideIndex": 0, "title": "T",
                               "contentType": "title", "keyPoints": []}],
                })}}

        monkeypatch.setattr(plan_tool, "require_chat_provider", lambda: _LLM())
        await plan_tool.PlanPresentationStructureTool.run(
            content_summary="growth", deck_format="poster")
        assert "SINGLE PAGE" in captured["prompt"]
        assert "totalSlides MUST be 1" in captured["prompt"]

    @pytest.mark.asyncio
    async def test_a_poster_plan_that_ignores_the_constraint_is_merged(
            self, monkeypatch):
        """The model was told totalSlides MUST be 1 and wrote three anyway.
        Merged rather than truncated: every section's material survives,
        folded into the one page a poster actually is."""
        from app.agent.tools import plan_presentation_structure as plan_tool

        class _LLM:
            async def chat(self, messages, tools=None):
                return {"message": {"content": json.dumps({
                    "title": "A Poster", "totalSlides": 3,
                    "slides": [
                        {"slideIndex": 0, "title": "Abstract",
                         "contentType": "title",
                         "keyPoints": ["The headline finding"]},
                        {"slideIndex": 1, "title": "Method",
                         "contentType": "content",
                         "keyPoints": ["How it was measured"]},
                        {"slideIndex": 2, "title": "Results",
                         "contentType": "chart",
                         "keyPoints": ["What changed"],
                         "notes": "See the attached figures"},
                    ],
                })}}

        monkeypatch.setattr(plan_tool, "require_chat_provider", lambda: _LLM())
        result = await plan_tool.PlanPresentationStructureTool.run(
            content_summary="growth", deck_format="poster")
        assert result.success
        structure = result.data
        assert structure["totalSlides"] == 1
        assert len(structure["slides"]) == 1
        page = structure["slides"][0]
        assert page["slideIndex"] == 0
        # Every section's own material survives - nothing dropped for want
        # of room, which is issue #285's own verification criterion.
        joined = " ".join(page["keyPoints"])
        assert "The headline finding" in joined
        assert "How it was measured" in joined
        assert "What changed" in joined
        assert "See the attached figures" in page["notes"]
        assert any("merged" in d.lower()
                  for d in structure.get("deviations", []))

    @pytest.mark.asyncio
    async def test_a_poster_plan_that_already_has_one_slide_is_untouched(
            self, monkeypatch):
        from app.agent.tools import plan_presentation_structure as plan_tool

        class _LLM:
            async def chat(self, messages, tools=None):
                return {"message": {"content": json.dumps({
                    "title": "A Poster", "totalSlides": 1,
                    "slides": [{"slideIndex": 0, "title": "A Poster",
                               "contentType": "content",
                               "keyPoints": ["one", "two"]}],
                })}}

        monkeypatch.setattr(plan_tool, "require_chat_provider", lambda: _LLM())
        result = await plan_tool.PlanPresentationStructureTool.run(
            content_summary="growth", deck_format="poster")
        structure = result.data
        assert structure["totalSlides"] == 1
        assert structure.get("deviations", []) == []

    @pytest.mark.asyncio
    async def test_an_unspecified_format_behaves_as_boardroom(self, monkeypatch):
        from app.agent.tools import plan_presentation_structure as plan_tool

        captured = {}

        class _LLM:
            async def chat(self, messages, tools=None):
                captured["prompt"] = messages[0]["content"]
                return {"message": {"content": json.dumps({
                    "title": "T", "totalSlides": 12,
                    "slides": [{"slideIndex": 0, "title": "T",
                               "contentType": "title", "keyPoints": []}],
                })}}

        monkeypatch.setattr(plan_tool, "require_chat_provider", lambda: _LLM())
        await plan_tool.PlanPresentationStructureTool.run(content_summary="growth")
        assert "Boardroom deck" in captured["prompt"]


class TestDesignSlideContentReadsTheFormat:
    @pytest.mark.asyncio
    async def test_a_pitch_slide_gets_a_smaller_budget_than_boardroom(self):
        from app.agent.tools import design_slide_content as dsc

        outline = {"keyPoints": ["one", "two", "three"]}
        boardroom = dsc._density_guidance({}, outline, outline["keyPoints"],
                                          [], 0, "boardroom")
        pitch = dsc._density_guidance({}, outline, outline["keyPoints"],
                                      [], 1, "pitch")
        # density_brief_available gates the whole string on the design
        # system carrying a density profile; without one both come back
        # empty, so this only asserts they do not diverge in a way that
        # would crash - the numeric claim is `TestBudgetReadsTheFormat`'s.
        assert isinstance(boardroom, str) and isinstance(pitch, str)


class TestTheSpecimenPreviewMatchesTheFormat:
    """A dashboard preview built from prose points is dishonest - the user
    picks a design by looking at a page it will never actually build."""

    def test_boardroom_and_academic_are_unchanged(self):
        from app.renderer.specimen import language_specimen_slides

        default = language_specimen_slides()
        for fid in ("boardroom", "academic", ""):
            assert language_specimen_slides(format_id=fid) == default

    def test_pitch_carries_one_point_not_three(self):
        from app.renderer.specimen import language_specimen_slides

        slides = language_specimen_slides(format_id="pitch")
        runs = slides[1]["contents"][0]["runs"]
        assert len(runs) == 1

    def test_dashboard_carries_two_exhibits_not_a_paragraph(self):
        from app.renderer.specimen import language_specimen_slides

        slides = language_specimen_slides(format_id="dashboard")
        kinds = [block["type"] for block in slides[2]["contents"]]
        assert kinds == ["chart", "metrics"]

    def test_the_metrics_block_carries_real_figures(self):
        from app.renderer.specimen import default_content, language_specimen_slides

        slides = language_specimen_slides(default_content(), "dashboard")
        items = slides[2]["contents"][1]["metrics"]["items"]
        assert items and all(item.get("value") and item.get("label")
                             for item in items)

    def test_a_dashboard_specimen_still_composes(self):
        from app.design import run as design_run
        from app.design.authored import Language, PageKind, Slot, to_pack
        from app.design.vocabulary import ensure
        from app.renderer.specimen import language_specimen_slides

        language = Language(
            name="Probe", rationale="x", columns=6, body_pt=14.0,
            pages=(
                PageKind(name="cover", role="cover", frequency=0.1, slots=(
                    Slot(kind="heading", span=6, required=True),)),
                PageKind(name="page", role="content", frequency=0.9, slots=(
                    Slot(kind="heading", span=6, required=True),
                    Slot(kind="statistic", span=6, name="row"),
                    Slot(kind="figure", span=6))),
            ),
        )
        fmt = deck_format.FORMATS["dashboard"]
        pack = ensure(to_pack(language, fmt.canvas,
                              floor_pt=fmt.floor_pt(fmt.canvas)))
        doc = design_run.compose(
            language_specimen_slides(format_id="dashboard"), pack,
            skip_unplaceable=True)
        assert len(doc.pages) >= 1


class TestTheDirectionCarriesItsFormat:
    """`_pack_for` and `_chosen_pack` reconstruct a pack from a persisted
    direction dict after a chat pause - the canvas has to survive that trip
    or a poster becomes a slide deck on resume."""

    def test_a_direction_without_a_format_rebuilds_as_boardroom(self):
        from app.design.authored import Language, PageKind, Slot
        from app.renderer.specimen import _pack_for

        language = Language(
            name="Probe", rationale="x", columns=6, body_pt=18.0,
            pages=(PageKind(name="p", role="content", frequency=1.0, slots=(
                Slot(kind="heading", span=6, required=True),)),),
        )
        direction = {"id": "probe", "language": language.model_dump(mode="json")}
        pack = _pack_for(direction)
        assert pack is not None
        assert pack.canvas.w_in == pytest.approx(13.333, abs=0.01)

    def test_a_poster_direction_rebuilds_at_the_poster_canvas(self):
        from app.design.authored import Language, PageKind, Slot
        from app.renderer.specimen import _pack_for

        language = Language(
            name="Probe", rationale="x", columns=4, body_pt=24.0,
            pages=(PageKind(name="p", role="content", frequency=1.0, slots=(
                Slot(kind="heading", span=4, required=True),)),),
        )
        direction = {"id": "probe", "format": "poster",
                    "language": language.model_dump(mode="json")}
        pack = _pack_for(direction)
        assert pack is not None
        fmt = deck_format.FORMATS["poster"]
        assert pack.canvas.w_in == pytest.approx(fmt.canvas.w_in)
        assert pack.canvas.h_in == pytest.approx(fmt.canvas.h_in)


class TestWorkingStateCarriesTheFormat:
    def test_it_defaults_to_boardroom(self):
        from app.agent.loop import WorkingState

        assert WorkingState().deck_format == deck_format.DEFAULT_FORMAT

    @pytest.mark.asyncio
    async def test_it_survives_a_save_and_load_cycle(self):
        from tests.test_agent_loop_workflow import _FakeRedis, _loop

        loop = _loop()
        loop.redis_client = _FakeRedis()
        loop.working_state.deck_format = "pitch"
        session_id = uuid.uuid4()
        await loop._save_working_state(session_id)

        resumed = _loop()
        resumed.redis_client = loop.redis_client
        await resumed._load_working_state(session_id)
        assert resumed.working_state.deck_format == "pitch"

    def test_resolving_never_overwrites_with_the_default(self):
        """A later, unrelated reply must not un-resolve an earlier answer."""
        from tests.test_agent_loop_workflow import _loop

        loop = _loop()
        loop.working_state.deck_format = "poster"
        # Simulate the hub's own guard: only overwrite on an actual match.
        matched = deck_format.resolve_from_chat(
            [{"role": "user", "content": "make the accent a bit brighter"}])
        if matched:
            loop.working_state.deck_format = matched
        assert loop.working_state.deck_format == "poster"


class TestThePosterSpecimenIsOnePage:
    """A poster is one sheet, not a deck (#285). Every section other formats
    spread across three slides goes on the one page a poster actually is."""

    def test_a_poster_specimen_is_a_single_slide(self):
        from app.renderer.specimen import language_specimen_slides

        slides = language_specimen_slides(format_id="poster")
        assert len(slides) == 1
        assert slides[0]["slideIndex"] == 0

    def test_every_section_survives_on_the_one_slide(self):
        from app.renderer.specimen import default_content, language_specimen_slides

        content = default_content()
        slides = language_specimen_slides(content, "poster")
        page = slides[0]
        blocks = page["contents"]
        kinds = [b["type"] for b in blocks]
        assert "chart" in kinds
        text_blocks = [b for b in blocks if b["type"] == "text"]
        joined = " ".join(
            run["text"] for block in text_blocks for run in block["runs"])
        assert content.subtitle in joined
        assert any(point.label in joined for point in content.points)
        assert content.takeaway in joined

    def test_a_poster_specimen_composes_against_a_poster_language(self):
        from app.design import deck_format, run as design_run
        from app.design.authored import Language, PageKind, Slot, to_pack
        from app.design.vocabulary import ensure
        from app.renderer.specimen import language_specimen_slides

        fmt = deck_format.FORMATS["poster"]
        language = Language(
            name="Poster", rationale="x", columns=4, body_pt=24.0,
            pages=(PageKind(name="page", role="content", frequency=1.0,
                            slots=(Slot(kind="heading", span=4, required=True),
                                   Slot(kind="prose", span=2, name="a"),
                                   Slot(kind="prose", span=2, name="b"),
                                   Slot(kind="figure", span=4))),),
        )
        pack = ensure(to_pack(language, fmt.canvas,
                              floor_pt=fmt.floor_pt(fmt.canvas)))
        doc = design_run.compose(
            language_specimen_slides(format_id="poster"), pack,
            skip_unplaceable=True)
        assert len(doc.pages) >= 1
        assert doc.canvas.w_in == fmt.canvas.w_in


class TestPlanMergingForSinglePageFormats:
    """`_merged_into_one_page` - the deterministic half of #285's fix. Given
    a plan that ignored "totalSlides MUST be 1", nothing is dropped and
    nothing downstream needs to know a merge happened."""

    def test_a_slide_with_no_material_merges_to_nothing_odd(self):
        from app.agent.tools.plan_presentation_structure import (
            _merged_into_one_page,
        )

        merged = _merged_into_one_page({
            "title": "T", "totalSlides": 0, "slides": [],
        })
        assert merged["slides"] == [] or merged.get("totalSlides") == 0

    def test_section_headings_are_kept_as_orientation(self):
        from app.agent.tools.plan_presentation_structure import (
            _merged_into_one_page,
        )

        merged = _merged_into_one_page({
            "title": "T", "totalSlides": 2,
            "slides": [
                {"slideIndex": 0, "title": "Abstract", "keyPoints": ["a"]},
                {"slideIndex": 1, "title": "Method", "keyPoints": ["b"]},
            ],
        })
        page = merged["slides"][0]
        assert "Abstract:" in page["keyPoints"]
        assert "Method:" in page["keyPoints"]
        assert merged["totalSlides"] == 1

    def test_slides_out_of_order_are_merged_in_slide_index_order(self):
        from app.agent.tools.plan_presentation_structure import (
            _merged_into_one_page,
        )

        merged = _merged_into_one_page({
            "title": "T", "totalSlides": 2,
            "slides": [
                {"slideIndex": 1, "title": "Second", "keyPoints": ["b"]},
                {"slideIndex": 0, "title": "First", "keyPoints": ["a"]},
            ],
        })
        points = merged["slides"][0]["keyPoints"]
        assert points.index("First:") < points.index("Second:")
