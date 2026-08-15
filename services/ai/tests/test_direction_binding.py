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

    def test_declining_the_consultation_skips_both_pauses(self):
        from app.agent import pipeline

        loop = _loop()
        loop.working_state.brief = {"needsResearch": False}
        loop.questions_declined = True

        assert pipeline.next_phase(loop).name == "plan"

    def test_handing_over_the_decisions_skips_only_the_design_pause(self):
        """The outline review survives it - seeing the slide titles is the
        cheapest moment to redirect a deck."""
        from app.agent import pipeline

        loop = _loop()
        loop.working_state.brief = {"needsResearch": False}
        loop.working_state.consulted_before_planning = True
        loop.working_state.autonomy_confirmed = True

        assert pipeline.next_phase(loop).name == "plan"

        loop.working_state.presentation_structure = {"totalSlides": 1, "slides": [{}]}
        assert pipeline.next_phase(loop).name == "review"

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

    def test_an_answer_matching_nothing_leaves_the_run_alone(self):
        """An unrecognised reply must not silently pick a direction."""
        loop = self._offered(_loop())

        assert loop._resolve_direction_choice(
            [{"role": "user", "content": "actually make it purple"}]
        ) is None

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
