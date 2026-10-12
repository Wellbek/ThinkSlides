"""
The run that builds in the direction the user picked.

Two problems, both about state surviving a pause. The chosen direction has to
reach the planner, the layout selector, the content designer and the renderer,
or picking a design system is worthless - which is exactly the failure #20
fixed for design-system context. And the loop's bookkeeping was written for a
single consultation, while a run now pauses twice: the text questions, then
the three rendered directions.

The direction pause is deliberately its own phase rather than another entry in
``answered_questions``, which is replayed into the prompt as "do NOT ask these
questions again" - a second pause suppressed by the first one having happened
is the bug that shape invites.
"""

import uuid

import pytest


def _loop():
    from app.agent.loop import AgentLoop, WorkingState

    loop = AgentLoop.__new__(AgentLoop)
    loop.questions_declined = False
    loop.working_state = WorkingState()
    loop.mode = "simple"
    loop.search_config = None
    return loop

class TestARunPausesTwice:
    def test_directions_are_out_of_reach_before_the_questions(self):
        """Every answer from the consultation constrains all three directions,
        so proposing them first would be proposing them uninformed."""
        from app.agent import pipeline

        loop = _loop()
        loop.working_state.brief = {"needsResearch": False}

        assert pipeline.next_phase(loop).name == "consult"

    def test_they_are_what_comes_next_once_the_questions_are_answered(self):
        from app.agent import pipeline

        loop = _loop()
        loop.working_state.brief = {"needsResearch": False}
        loop.working_state.consulted_before_planning = True

        assert pipeline.next_phase(loop).name == "design"

    def test_planning_waits_for_the_direction_pause(self):
        from app.agent import pipeline

        loop = _loop()
        loop.working_state.brief = {"needsResearch": False}
        loop.working_state.consulted_before_planning = True

        assert pipeline.next_phase(loop).name != "plan"

    def test_a_caller_that_cannot_answer_is_not_asked(self):
        """The offline harness and the tests, not a user: a user who wants the
        pauses off confirms it (see test_pipeline). The design still gets
        settled - it is just settled without them."""
        from app.agent import pipeline

        loop = _loop()
        loop.working_state.brief = {"needsResearch": False}
        loop.questions_declined = True

        assert pipeline.next_phase(loop).name == "design"

        loop.working_state.offered_directions = True
        assert pipeline.next_phase(loop).name == "plan"

    def test_a_confirmed_handover_ends_the_pauses_but_not_the_design(self):
        """It is the one thing that turns the consultation off - including the
        outline review. The design is still settled, just not by asking."""
        from app.agent import pipeline

        loop = _loop()
        loop.working_state.brief = {"needsResearch": False}
        loop.working_state.consulted_before_planning = True
        loop.working_state.autonomy_confirmed = True

        assert pipeline.next_phase(loop).name == "design"

        loop.working_state.offered_directions = True
        loop.working_state.presentation_structure = {"totalSlides": 1, "slides": [{}]}
        assert pipeline.next_phase(loop).name == "draft"

    def test_the_direction_pause_is_not_recorded_as_a_question(self):
        """answered_questions is replayed as "do NOT ask these again"; the
        direction pause going in there would suppress a pause that has not
        happened yet."""
        loop = _loop()
        loop.working_state.offered_directions = True

        assert loop.working_state.answered_questions == []


class TestTheChoiceGovernsTheRun:
    @staticmethod
    def _offered(loop):
        loop.working_state.offered_directions = True
        loop.working_state.proposed_directions = [
            {"id": "dark-analyst", "name": "Dark analyst", "rationale": "r",
             "designSystem": {"marker": "dark"}},
            {"id": "clean-minimal", "name": "Clean minimal", "rationale": "r",
             "designSystem": {"marker": "clean"}},
        ]
        return loop

    def test_the_id_the_card_sends_resolves_the_direction(self):
        loop = self._offered(_loop())

        chosen = loop._resolve_direction_choice(
            [{"role": "user", "content": "Dark analyst (direction: dark-analyst)"}]
        )

        assert chosen["designSystem"]["marker"] == "dark"

    def test_the_name_alone_still_resolves_it(self):
        loop = self._offered(_loop())

        chosen = loop._resolve_direction_choice(
            [{"role": "user", "content": "let's go with clean minimal please"}]
        )

        assert chosen["designSystem"]["marker"] == "clean"

    def test_an_answer_matching_nothing_is_read_as_deferring_the_choice(self):
        """
        This used to resolve to None, so that an unrecognised reply could not
        silently pick a direction. The principle stands; the outcome did not.
        The render guard refuses unless a direction was chosen, so a user who
        answered in their own words wedged the run and got no deck at all -
        see #258.

        The pick is no longer silent: `direction_deferred` is set and the run
        announces which direction it used and that it chose on the user's
        behalf. Note this reply also asks for a colour, which
        `_apply_colour_choice` then applies to the direction picked here.
        """
        loop = self._offered(_loop())

        chosen = loop._resolve_direction_choice(
            [{"role": "user", "content": "actually make it purple"}]
        )

        assert chosen is not None
        assert loop.working_state.direction_deferred is True

    def test_a_deferral_is_only_read_once_the_user_has_actually_replied(self):
        """The guard still fires while the question is outstanding: defaulting
        then would pick a look nobody has been shown the question for."""
        loop = self._offered(_loop())

        assert loop._resolve_direction_choice([
            {"role": "user", "content": "build me a deck"},
            {"role": "assistant", "content": "Which direction shall I use?"},
        ]) is None

    def test_a_choice_already_made_is_not_remade(self):
        loop = self._offered(_loop())
        loop.working_state.chosen_direction = {"id": "dark-analyst"}

        assert loop._resolve_direction_choice(
            [{"role": "user", "content": "clean minimal"}]
        ) is None

    def test_nothing_resolves_before_anything_was_offered(self):
        assert _loop()._resolve_direction_choice(
            [{"role": "user", "content": "dark analyst"}]
        ) is None

    def test_the_colour_answered_beside_it_is_applied_to_it(self):
        """Both questions ride on one card, so the colour necessarily lands
        after the specimens were drawn. It catches up here."""
        from app import directions as direction_model

        loop = _loop()
        compiled, _ = direction_model.validate([direction_model.DirectionSpec(
            id="dark-analyst", name="Dark analyst", rationale="r", ground="dark",
        )])

        chosen, applied = loop._apply_colour_choice(
            compiled[0],
            [{"role": "user", "content": "Dark analyst (direction: dark-analyst)\n"
                                         "And the colour? Forest green (#1B4332)"}],
        )

        assert applied == "#1B4332"
        assert {c["name"]: c["value"] for c in
                chosen["designSystem"]["palette"]["colors"]}["primary"] == "#1B4332"

    def test_as_proposed_carries_no_colour_and_changes_nothing(self):
        loop = _loop()
        offered = {"id": "d", "designSystem": {"marker": "kept"}}

        chosen, applied = loop._apply_colour_choice(
            offered,
            [{"role": "user", "content": "Dark analyst (direction: dark-analyst)\n"
                                         "And the colour? As proposed"}],
        )

        assert applied is None
        assert chosen is offered

    def test_a_colour_settled_before_the_pause_is_not_applied_twice(self):
        """All three directions were already built from it, so re-reading it
        off an older message would recompile for no reason."""
        loop = _loop()
        offered = {"id": "d", "designSystem": {"marker": "kept"}}

        chosen, applied = loop._apply_colour_choice(offered, [
            {"role": "user", "content": "Use #00704A"},
            {"role": "assistant", "content": "Which direction?"},
            {"role": "user", "content": "Clean minimal (direction: clean-minimal)"},
        ])

        assert applied is None
        assert chosen is offered

    def test_the_choice_survives_the_pause_it_was_made_in(self):
        """Killing the worker between the direction pause and generation must
        not lose the thing the user explicitly asked for."""
        from app.agent.loop import WorkingState

        before = self._offered(_loop()).working_state
        before.chosen_direction = {"id": "dark-analyst",
                                   "designSystem": {"marker": "dark"}}
        saved = {
            "offered_directions": before.offered_directions,
            "chosen_direction": before.chosen_direction,
            "proposed_directions": before.proposed_directions,
        }

        after = WorkingState()
        after.offered_directions = saved["offered_directions"]
        after.chosen_direction = saved["chosen_direction"]
        after.proposed_directions = saved["proposed_directions"]

        assert after.chosen_direction["designSystem"]["marker"] == "dark"
        assert after.offered_directions is True

    @pytest.mark.asyncio
    async def test_the_checkpoint_state_round_trips_through_redis(self):
        import json as _json

        from app.agent.loop import AgentLoop, WorkingState

        class _Redis:
            def __init__(self):
                self.store = {}

            async def setex(self, key, ttl, value):
                self.store[key] = value

            async def set(self, key, value, ex=None):
                self.store[key] = value

            async def get(self, key):
                return self.store.get(key)

            async def delete(self, key):
                self.store.pop(key, None)

        loop = AgentLoop.__new__(AgentLoop)
        loop.working_state = WorkingState()
        loop.mode = "simple"
        loop.context = {}
        loop.redis_client = _Redis()
        loop.detail_level = None
        loop.include_sources = False
        loop._resumed_design_system = None
        self._offered(loop)
        loop.working_state.chosen_direction = {
            "id": "dark-analyst", "designSystem": {"marker": "dark"}}

        session_id = uuid.uuid4()
        await loop._save_working_state(session_id)
        saved = _json.loads(loop.redis_client.store[f"working_state:{session_id}"])

        assert saved["offered_directions"] is True
        assert saved["chosen_direction"]["designSystem"]["marker"] == "dark"
        assert len(saved["proposed_directions"]) == 2


class TestTheDeckKeepsTheShapeTheUserSaw:
    """The user picked a direction by looking at what its points *looked*
    like. A deck that then writes bullets whatever they chose makes the
    preview a lie."""

    @staticmethod
    def _drafting(structure: str):
        loop = _loop()
        loop.working_state.presentation_structure = {
            "totalSlides": 1, "slides": [{"title": "One"}]
        }
        loop.working_state.chosen_direction = {
            "id": "d", "designSystem": {"structure": structure}
        }
        return loop

    def test_a_banded_direction_asks_for_row_bands(self):
        assert "build_row_bands" in self._drafting("bands").slide_directive()

    def test_a_figure_led_direction_asks_for_stat_callouts(self):
        assert "build_stat_callouts" in self._drafting("stats").slide_directive()

    def test_a_card_led_direction_asks_for_accent_cards(self):
        assert "build_accent_cards" in self._drafting("cards").slide_directive()

    def test_a_prose_direction_asks_for_no_block_in_particular(self):
        directive = self._drafting("list").slide_directive()

        assert "written prose" in directive
        assert "build_row_bands" not in directive

    def test_a_run_with_no_chosen_direction_says_nothing_extra(self):
        loop = _loop()
        loop.working_state.presentation_structure = {
            "totalSlides": 1, "slides": [{"title": "One"}]
        }

        assert "direction" not in loop.slide_directive()

    def test_every_structure_a_direction_can_take_has_a_directive(self):
        """A shape with nothing said about it is a preview the deck cannot
        honour."""
        from app import directions as direction_model
        from app.agent.loop import _STRUCTURE_DIRECTIVE

        assert set(direction_model.STRUCTURES) == set(_STRUCTURE_DIRECTIVE)


class TestTheDraftTurnHearsWhatThePlanAlreadyDecided:
    """#289: plan_presentation_structure reasons about whether a slide's key
    points are chart or table material, or need several exhibits, and
    records that as `contentType`/`exhibitPlan` on the plan - and until this,
    none of it reached the turn that actually drafts the slide, which had to
    notice the same thing again from a bare title and generic advice, and
    evidently often didn't."""

    @staticmethod
    def _drafting(content_type: str, **planned_overrides):
        loop = _loop()
        planned = {"title": "One", "contentType": content_type}
        planned.update(planned_overrides)
        loop.working_state.presentation_structure = {
            "totalSlides": 1, "slides": [planned],
        }
        return loop

    def test_a_chart_slide_is_told_to_build_one(self):
        directive = self._drafting("chart").slide_directive()
        assert "build_chart" in directive
        assert "plan calls for this to be a chart" in directive

    def test_a_table_slide_is_told_to_build_one(self):
        directive = self._drafting("table").slide_directive()
        assert "build_table" in directive

    def test_an_image_slide_is_told_to_find_one(self):
        directive = self._drafting("image").slide_directive()
        assert "select_source_image" in directive

    def test_a_dashboard_slide_is_told_to_build_one_exhibit_per_question(self):
        directive = self._drafting(
            "dashboard",
            exhibitPlan=["What are the headline figures?",
                         "Where did the movement come from?"],
        ).slide_directive()
        assert "What are the headline figures?" in directive
        assert "Where did the movement come from?" in directive
        assert "build_chart, build_table, build_key_metrics or build_stat_callouts" in directive

    def test_a_dashboard_slide_with_no_recorded_plan_still_says_something(self):
        directive = self._drafting("dashboard").slide_directive()
        assert "no exhibit plan was recorded" in directive

    def test_an_ordinary_content_slide_gets_no_content_type_push(self):
        """The push is for the cases the plan singled out - an ordinary
        slide is not told to build anything it wasn't planned to have."""
        directive = self._drafting("content").slide_directive()
        assert "build_chart" not in directive
        assert "build_table" not in directive
        assert "plan calls for this to be" not in directive

    def test_every_draft_turn_states_the_decks_own_vocabulary(self):
        """Most slides are not singled out by contentType, and those are
        exactly the ones with nothing at draft time distinguishing a
        boardroom page from an academic one - unless the format's own
        vocabulary is restated regardless of contentType."""
        from app.design import deck_format

        loop = self._drafting("content")
        loop.working_state.deck_format = deck_format.BOARDROOM.id

        assert deck_format.BOARDROOM.vocabulary in loop.slide_directive()

    def test_a_different_format_states_its_own_vocabulary_not_boardrooms(self):
        from app.design import deck_format

        loop = self._drafting("content")
        loop.working_state.deck_format = deck_format.DASHBOARD.id
        directive = loop.slide_directive()

        assert deck_format.DASHBOARD.vocabulary in directive
        assert deck_format.BOARDROOM.vocabulary not in directive
