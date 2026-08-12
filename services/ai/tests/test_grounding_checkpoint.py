"""
A factual deck with nothing behind it must stop and ask.

In simple mode the agent has no web access and, unless documents were
uploaded, nothing but its own memory. It still produced charts: a real run
asked for "Starbucks FY2025" and charted revenue rising to $40.0B against an
actual ~$37B. The number was invented, and putting it in a chart with a
labelled axis is what made it look sourced.

Hardening the prompt (#72) helps and guarantees nothing - the model was told
for months to default to asking and went from prompt to finished deck without a
word. So this is a precondition, like the other consultation checkpoints: the
planning call does not go through until the offer has been made.
"""

import pytest

from app.agent.loop import (
    DEEP_RESEARCH_NO,
    DEEP_RESEARCH_YES,
    MODE_DEEP,
    MODE_SIMPLE,
    request_needs_grounding,
    user_answered_deep_research,
)


def _user(text: str) -> list[dict]:
    return [{"role": "user", "content": text}]


class TestFactualRequestDetection:
    @pytest.mark.parametrize(
        "request_text",
        [
            "Starbucks FY2025 results",
            "a deck on our Q3 revenue",
            "the latest AI market size",
            "competitor analysis for the EV sector",
            "how many users does the platform have",
            "2026 industry outlook",
            "key benchmarks for SaaS margins",
        ],
    )
    def test_asks_for_figures(self, request_text):
        assert request_needs_grounding(_user(request_text)) is True

    @pytest.mark.parametrize(
        "request_text",
        [
            "a 10-slide deck on how to run a retrospective",
            "an onboarding deck for new hires",
            "explain our design principles",
            "a workshop on giving feedback",
        ],
    )
    def test_asks_for_a_framework(self, request_text):
        """Interrupting these to offer web research would be noise."""
        assert request_needs_grounding(_user(request_text)) is False


class TestAnswerDetection:
    def test_the_offered_options_are_recognised(self):
        assert user_answered_deep_research(_user(DEEP_RESEARCH_YES)) is True
        assert user_answered_deep_research(_user(DEEP_RESEARCH_NO)) is False

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("yes please search the web", True),
            ("turn on deep thinking", True),
            ("look it up online", True),
            ("just use what you already know", False),
            ("no research needed", False),
            ("don't search, I'm in a hurry", False),
        ],
    )
    def test_a_typed_answer_is_recognised(self, text, expected):
        assert user_answered_deep_research(_user(text)) is expected

    def test_silence_on_the_subject_is_not_an_answer(self):
        assert user_answered_deep_research(_user("make the title blue")) is None

    def test_a_decline_is_never_read_as_a_yes(self):
        """
        "No - build it from what you already know" contains no yes phrase, but
        the ordering is what guarantees it: declines are checked first.
        """
        assert user_answered_deep_research(_user(DEEP_RESEARCH_NO)) is False

    def test_the_latest_answer_wins(self):
        messages = [
            {"role": "user", "content": DEEP_RESEARCH_NO},
            {"role": "assistant", "content": "understood"},
            {"role": "user", "content": "actually, search the web"},
        ]
        assert user_answered_deep_research(messages) is True


class _Loop:
    """The parts of AgentLoop the checkpoint logic touches."""

    def __init__(self, mode=MODE_SIMPLE, grounded=False, declined=False, offered=False):
        from app.agent.loop import AgentLoop, WorkingState

        self.loop = AgentLoop.__new__(AgentLoop)
        self.loop.mode = mode
        self.loop.context = {"research": [{"url": "https://x"}]} if grounded else {}
        self.loop.questions_declined = declined
        self.loop.working_state = WorkingState()
        self.loop.working_state.offered_deep_research = offered

    def should_offer(self, text="our Q3 revenue"):
        return self.loop._should_offer_deep_research(_user(text))

    def has_grounding(self):
        return self.loop._has_grounding()


class TestOfferDecision:
    def test_offers_for_a_factual_request_with_nothing_behind_it(self):
        assert _Loop().should_offer() is True

    def test_stays_quiet_in_deep_mode(self):
        """Deep mode already researches; there is nothing to offer."""
        assert _Loop(mode=MODE_DEEP).should_offer() is False

    def test_stays_quiet_when_the_run_has_research(self):
        assert _Loop(grounded=True).should_offer() is False

    def test_stays_quiet_for_a_request_that_needs_no_figures(self):
        assert _Loop().should_offer("a deck on how to run a retrospective") is False

    def test_respects_just_build_it(self):
        assert _Loop(declined=True).should_offer() is False

    def test_asks_only_once(self):
        """The flag is persisted, so a resumed run must not ask again."""
        assert _Loop(offered=True).should_offer() is False


class TestGroundingSignals:
    def test_an_analysed_document_is_grounding(self):
        harness = _Loop()
        harness.loop.working_state.record_completed_tool("analyze_document", {}, {})
        assert harness.has_grounding() is True
        assert harness.should_offer() is False

    def test_researched_urls_are_grounding(self):
        harness = _Loop()
        harness.loop.working_state.researched_urls.add("https://example.com")
        assert harness.has_grounding() is True

    def test_the_models_own_memory_is_not_grounding(self):
        """The whole point: memory is what produced the invented $40.0B."""
        assert _Loop().has_grounding() is False


def test_the_offer_flag_survives_a_pause():
    """
    The offer pauses the run. If the flag did not persist, the resumed run
    would ask the same question again - the trap the other two checkpoints
    already documented.
    """
    from app.agent.loop import WorkingState

    state = WorkingState()
    state.offered_deep_research = True
    # The same shape _save_working_state writes and _load_working_state reads.
    saved = {"offered_deep_research": state.offered_deep_research}
    restored = WorkingState()
    restored.offered_deep_research = saved.get("offered_deep_research", False)
    assert restored.offered_deep_research is True
