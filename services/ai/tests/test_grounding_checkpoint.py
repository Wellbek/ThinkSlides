"""
A factual deck with nothing behind it must stop and ask.

In simple mode the agent has no web access and, unless documents were
uploaded, nothing but its own memory. It still produced charts: a real run
asked for "Starbucks FY2025" and charted revenue rising to $40.0B against an
actual ~$37B. The number was invented, and putting it in a chart with a
labelled axis is what made it look sourced.

Two mechanisms, and the split between them is still the design:

* Whether the deck needs looking up is a *judgement*, so a model makes it -
  now as part of the intake step that opens every run, rather than as a
  separate classification call.
* Whether the user then gets asked is *not* a judgement, and never was left to
  the model. The offer rides in the one consultation pause the pipeline makes,
  which costs it no round trip of its own.
"""

import json
import uuid

import pytest

from app.agent.loop import (
    DEEP_RESEARCH_NO,
    DEEP_RESEARCH_YES,
    MODE_DEEP,
    MODE_SIMPLE,
    AgentLoop,
    user_answered_deep_research,
)


def _user(text: str) -> list[dict]:
    return [{"role": "user", "content": text}]


class TestAnswerDetection:
    def test_the_offered_options_are_recognised(self):
        assert user_answered_deep_research(_user(DEEP_RESEARCH_YES)) is True
        assert user_answered_deep_research(_user(DEEP_RESEARCH_NO)) is False

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("yes please, search the web for it", True),
            ("go ahead and look it up online", True),
            ("no, build it from what you already know", False),
            ("don't search, just write it", False),
        ],
    )
    def test_a_typed_answer_is_recognised(self, text, expected):
        assert user_answered_deep_research(_user(text)) is expected

    def test_silence_on_the_subject_is_not_an_answer(self):
        assert user_answered_deep_research(_user("make it 10 slides")) is None

    def test_a_decline_is_never_read_as_a_yes(self):
        """"No - build it from what you already know" contains "know", and an
        earlier matcher read the "web" in the question it answered."""
        assert user_answered_deep_research(
            _user("No - build it from what you already know")
        ) is False

    def test_the_latest_answer_wins(self):
        messages = _user("no research please") + _user("actually, search the web")
        assert user_answered_deep_research(messages) is True


class TestTheQuestionItself:
    def _question(self, figures):
        return AgentLoop.__new__(AgentLoop)._deep_research_question(figures)

    def test_the_options_are_the_two_the_answer_matcher_knows(self):
        """The wording is matched verbatim on the way back. A model-authored
        variant would silently stop being recognised as an answer."""
        question = self._question([])
        assert question["options"] == [DEEP_RESEARCH_YES, DEEP_RESEARCH_NO]
        assert user_answered_deep_research(_user(question["options"][0])) is True
        assert user_answered_deep_research(_user(question["options"][1])) is False

    def test_the_missing_figures_are_named(self):
        question = self._question(["Starbucks FY2025 revenue", "store count"])
        assert "Starbucks FY2025 revenue, and store count" in question["context"]

    def test_a_single_figure_reads_as_a_sentence(self):
        question = self._question(["Starbucks FY2025 revenue"])
        assert "means Starbucks FY2025 revenue." in question["context"]

    def test_no_figures_still_produces_a_usable_question(self):
        question = self._question([])
        assert question["question"]
        assert "web search is off" in question["context"]


def _loop(mode=MODE_SIMPLE, **brief):
    loop = AgentLoop(db=None, mode=mode)
    loop.working_state.brief = {"needsResearch": True, **brief}
    loop.search_config = object()
    return loop


class TestWhenTheOfferIsMade:
    def test_a_factual_deck_with_no_web_access_gets_the_offer(self):
        assert _loop()._can_offer_research() is True

    def test_a_conceptual_deck_does_not(self):
        assert _loop(needsResearch=False)._can_offer_research() is False

    def test_neither_does_one_that_is_already_researching(self):
        """Deep Thinking is the answer to the question, already given."""
        assert _loop(mode=MODE_DEEP)._can_offer_research() is False

    def test_nor_one_with_no_backend_to_research_with(self):
        loop = _loop()
        loop.search_config = None

        assert loop._can_offer_research() is False

    def test_nor_one_that_has_already_asked(self):
        loop = _loop()
        loop.working_state.offered_deep_research = True

        assert loop._can_offer_research() is False


class TestGroundingSignals:
    def test_an_analysed_document_is_grounding(self):
        loop = AgentLoop(db=None, mode=MODE_SIMPLE)
        loop.working_state.record_completed_tool("analyze_document", {}, {})

        assert loop._has_grounding() is True

    def test_researched_urls_are_grounding(self):
        loop = AgentLoop(db=None, mode=MODE_SIMPLE)
        loop.working_state.researched_urls.add("https://example.com")

        assert loop._has_grounding() is True

    def test_the_models_own_memory_is_not_grounding(self):
        """The whole point: memory is what produced the invented $40.0B."""
        assert AgentLoop(db=None, mode=MODE_SIMPLE)._has_grounding() is False


class _FakeRedis:
    def __init__(self):
        self.store = {}

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, key):
        self.store.pop(key, None)


async def _async(value):
    return value


class TestTheOfferRidesInTheConsultation:
    """It used to be a pause of its own, before the first tool call. That is a
    round trip spent on one question, and the run already stops once to
    consult - so it goes there."""

    @pytest.mark.asyncio
    async def test_a_factual_simple_run_is_asked_about_research(
        self, monkeypatch
    ):
        from app.agent.registry import ToolResult

        session_id = uuid.uuid4()
        loop = AgentLoop(db=None, mode=MODE_SIMPLE, detail_level="detailed")
        loop.redis_client = _FakeRedis()
        loop.search_config = object()

        async def _chat(messages=None, tools=None):
            return {"message": {"content": json.dumps({
                "brief": "A deck on Starbucks FY2025.",
                "needsResearch": True,
                "wantsAutonomy": False,
                "figures": ["Starbucks FY2025 revenue"],
                "ambiguities": [],
            })}, "tool_calls": []}

        async def _execute(name, args):
            questions = args.get("questions") or []
            return ToolResult(success=True, data={
                "questions": questions,
                "question": questions[0]["question"],
                "options": questions[0]["options"],
                "context": args.get("context", ""),
            })

        monkeypatch.setattr(loop, "_chat_with_retry", _chat)
        monkeypatch.setattr(loop, "_execute_tool", _execute)
        monkeypatch.setattr(loop, "_load_context", lambda *a, **k: _async({}))
        monkeypatch.setattr(
            loop, "_build_chat_messages",
            lambda *a, **k: _async(_user("financial slides for starbucks")),
        )
        monkeypatch.setattr(loop, "_configure", lambda: _async(None))

        events = [e async for e in loop.run(session_id)]

        question = next(e for e in events if e["type"] == "question")
        # Only the research offer: this user implied nothing about handing over
        # and left nothing ambiguous, so nothing else is worth a round trip.
        assert len(question["questions"]) == 1
        assert question["questions"][0]["options"] == [DEEP_RESEARCH_YES, DEEP_RESEARCH_NO]
        assert events[-1]["paused"] is True


class TestADiagramIsNotAFigure:
    """
    The ungrounded-figures backstop covers the builders that present a number
    as measured. A diagram presents structure.

    "The request reaches the load balancer, then the application server, then
    the database" asserts no measurement, and it is usually the user's own
    architecture read back from their own brief. Blocking it meant process and
    architecture decks - the ones that most need a diagram and have no figures
    at all - silently fell back to three bullets and half an empty page, with
    the refusal reaching the model as advice about unverifiable numbers.

    A diagram that was given values or dates is a different thing, and is
    still caught.
    """

    def _blocked(self, fn_args: dict, tool: str = "build_diagram") -> bool:
        loop = AgentLoop(db=None, mode=MODE_SIMPLE)
        assert loop._has_grounding() is False
        message = loop._check_precondition(tool, fn_args)
        return bool(message and "no source behind this figure" in message)

    def test_a_flow_of_named_steps_is_allowed(self):
        assert self._blocked({"items": [
            {"label": "Request", "detail": "the user's device"},
            {"label": "Load balancer"},
            {"label": "Application server"},
        ]}) is False

    def test_a_timeline_carrying_dates_is_still_caught(self):
        assert self._blocked({"items": [
            {"label": "Pilot", "date": "Q1 2026"},
            {"label": "Rollout", "date": "Q3 2026"},
        ]}) is True

    def test_a_funnel_carrying_counts_is_still_caught(self):
        assert self._blocked({"items": [
            {"label": "Visitors", "value": "120,000"},
            {"label": "Signups", "value": "4,300"},
        ]}) is True

    def test_a_chart_is_caught_whatever_it_carries(self):
        assert self._blocked({"items": []}, tool="build_chart") is True

    def test_a_table_is_caught_whatever_it_carries(self):
        assert self._blocked({"items": []}, tool="build_table") is True
