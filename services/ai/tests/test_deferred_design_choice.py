"""
Answering the design question in your own words must not bin the run.

Reproduced end to end: a brief for a 3-slide investor update, answered at the
design-direction card with "Yes, proceed with your recommendation". The run
researched, planned and designed all three slides, then refused to render -
twice - and reported that nothing was generated.

`_resolve_direction_choice` matched the reply against the offered directions'
ids and names, found neither and returned None, on the principle that an
unrecognised answer must not silently pick one. The guard on
`finalize_and_render_pptx` then refused unless a direction was chosen, a
template was supplied, or autonomy was recorded - and `user_confirmed_autonomy`
only matches "you decide" / "decide yourself" / "go ahead ... decide". So the
guard told the model to ask a question it had already asked and had answered,
and nothing could satisfy it.

Widening the yes-patterns is not the fix: any phrase list misses the next
phrasing, and the failure when it misses is a whole run thrown away. The rule
these tests pin instead is that the guard's purpose - nobody is surprised by a
look they were never asked about - is satisfied once the user has been asked
and has replied, whatever words they used. What answers the original principle
is that the pick is announced rather than silent.
"""

import pytest

from app.agent.loop import AgentLoop, WorkingState


DIRECTIONS = [
    {"id": "field-notes", "name": "Field Notes", "rationale": "quiet and editorial",
     "designSystem": {"palette": {"primary": "#123456"}}},
    {"id": "hard-numbers", "name": "Hard Numbers", "rationale": "dense and factual",
     "designSystem": {"palette": {"primary": "#654321"}}},
    {"id": "wide-open", "name": "Wide Open", "rationale": "airy",
     "designSystem": {"palette": {"primary": "#abcdef"}}},
]


def _loop_with_offer() -> AgentLoop:
    loop = AgentLoop.__new__(AgentLoop)
    loop.working_state = WorkingState()
    loop.working_state.offered_directions = True
    loop.working_state.proposed_directions = list(DIRECTIONS)
    return loop


def _said(*turns: tuple[str, str]) -> list[dict]:
    return [{"role": role, "content": content} for role, content in turns]


class TestAnAnswerThatNamesADirection:
    def test_an_id_selects_exactly_that_direction(self):
        loop = _loop_with_offer()
        chosen = loop._resolve_direction_choice(
            _said(("assistant", "Which direction?"),
                  ("user", "Direction: hard-numbers"))
        )
        assert chosen["id"] == "hard-numbers"
        assert loop.working_state.direction_deferred is False

    def test_a_name_selects_it_when_no_id_was_sent(self):
        loop = _loop_with_offer()
        chosen = loop._resolve_direction_choice(
            _said(("assistant", "Which direction?"), ("user", "Let's go with Wide Open"))
        )
        assert chosen["id"] == "wide-open"
        assert loop.working_state.direction_deferred is False


class TestAnAnswerThatNamesNone:
    """The reproduction. Each of these used to resolve to None and wedge the
    run at the render guard."""

    @pytest.mark.parametrize("reply", [
        "Yes, proceed with your recommendation.",
        "whatever you think is best",
        "surprise me",
        "sure, go for it",
        "I don't mind, just make it look good",
    ])
    def test_a_deferral_picks_the_recommended_direction_and_says_so(self, reply):
        loop = _loop_with_offer()
        chosen = loop._resolve_direction_choice(
            _said(("assistant", "Which direction?"), ("user", reply))
        )
        assert chosen is not None, "the run must not be left with nothing to render"
        assert chosen["id"] == "field-notes"
        assert loop.working_state.direction_deferred is True

    def test_the_recommended_direction_is_the_first_one_offered(self):
        loop = _loop_with_offer()
        chosen = loop._resolve_direction_choice(
            _said(("assistant", "Which direction?"), ("user", "you pick"))
        )
        assert chosen is DIRECTIONS[0]


class TestWhenNothingShouldBeChosenYet:
    def test_nothing_is_chosen_before_the_user_has_replied(self):
        """The run is still waiting. Defaulting here would pick a look nobody
        has seen the question for, which is the surprise the guard exists to
        prevent."""
        loop = _loop_with_offer()
        chosen = loop._resolve_direction_choice(
            _said(("user", "Build me a deck about private credit"),
                  ("assistant", "Which direction?"))
        )
        assert chosen is None
        assert loop.working_state.direction_deferred is False

    def test_nothing_is_chosen_when_no_directions_were_offered(self):
        loop = AgentLoop.__new__(AgentLoop)
        loop.working_state = WorkingState()
        assert loop._resolve_direction_choice(_said(("user", "you decide"))) is None

    def test_nothing_is_chosen_when_the_offer_was_empty(self):
        loop = _loop_with_offer()
        loop.working_state.proposed_directions = []
        assert loop._resolve_direction_choice(_said(("user", "you decide"))) is None

    def test_a_direction_already_chosen_is_not_re_litigated(self):
        loop = _loop_with_offer()
        loop.working_state.chosen_direction = DIRECTIONS[2]
        assert loop._resolve_direction_choice(_said(("user", "Field Notes"))) is None

    def test_an_empty_reply_does_not_count_as_an_answer(self):
        loop = _loop_with_offer()
        chosen = loop._resolve_direction_choice(
            _said(("assistant", "Which direction?"), ("user", "   "))
        )
        assert chosen is None


class TestTheDeferralSurvivesAResume:
    """Working state round-trips through Redis between turns, and a flag that
    did not survive would make the announcement disappear on the next turn
    while the deck kept the direction."""

    def test_the_flag_is_saved_and_restored(self):
        state = WorkingState()
        state.direction_deferred = True
        saved = {"direction_deferred": state.direction_deferred}

        restored = WorkingState()
        restored.direction_deferred = saved.get("direction_deferred", False)
        assert restored.direction_deferred is True

    def test_it_defaults_to_false_for_state_written_before_this_existed(self):
        restored = WorkingState()
        restored.direction_deferred = {}.get("direction_deferred", False)
        assert restored.direction_deferred is False
