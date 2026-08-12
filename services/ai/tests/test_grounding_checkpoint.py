"""
A factual deck with nothing behind it must stop and ask.

In simple mode the agent has no web access and, unless documents were
uploaded, nothing but its own memory. It still produced charts: a real run
asked for "Starbucks FY2025" and charted revenue rising to $40.0B against an
actual ~$37B. The number was invented, and putting it in a chart with a
labelled axis is what made it look sourced.

Two mechanisms, and the split between them is the design:

* Whether the deck needs looking up is a *judgement*, so a model makes it. The
  first attempt was a regex over financial vocabulary and it missed the most
  ordinary phrasing there is - "financial slides for starbucks" matched
  nothing, because a word list only ever contains the words somebody thought of.
* Whether the user then gets asked is *not* a judgement. The loop emits the
  question itself, before the first tool call. Asking the model to ask did not
  work: it planned anyway, collected the refusal, and showed the user an error
  where a question belonged.
"""

import json
import uuid

import pytest

from app.agent.loop import (
    DEEP_RESEARCH_NO,
    DEEP_RESEARCH_YES,
    DRAFTING_TOOLS,
    MODE_DEEP,
    MODE_SIMPLE,
    PLANNING_TOOLS,
    _judge_messages,
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


class TestJudgePrompt:
    def test_only_the_users_words_are_judged(self):
        """
        The agent's own narration describes the deck it has already decided to
        build. Feeding that back reads as corroboration.
        """
        messages = _judge_messages([
            {"role": "user", "content": "financial slides for starbucks"},
            {"role": "assistant", "content": "I'll build a conceptual deck."},
        ])
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert "financial slides for starbucks" in messages[1]["content"]
        assert "conceptual deck" not in messages[1]["content"]

    def test_empty_turns_are_dropped(self):
        messages = _judge_messages([
            {"role": "user", "content": "   "},
            {"role": "user", "content": "Starbucks FY2025"},
        ])
        assert messages[1]["content"] == "Starbucks FY2025"

    def test_the_judge_is_told_to_name_the_missing_figures(self):
        system = _judge_messages(_user("anything"))[0]["content"]
        assert "needs_research" in system
        assert "figures" in system


class _FakeLLM:
    """Records what it was asked and replies with a canned verdict."""

    def __init__(self, reply: str | None = None, raises: Exception | None = None):
        self.reply = reply
        self.raises = raises
        self.calls: list[list[dict]] = []

    async def chat(self, messages, tools=None):
        self.calls.append(messages)
        if self.raises:
            raise self.raises
        return {"message": {"role": "assistant", "content": self.reply}, "tool_calls": []}


class _Loop:
    """The parts of AgentLoop the checkpoint logic touches."""

    def __init__(
        self,
        mode=MODE_SIMPLE,
        grounded=False,
        declined=False,
        offered=False,
        documents=False,
        verdict='{"needs_research": true, "figures": ["Starbucks FY2025 revenue"]}',
        raises=None,
        search_backend=True,
        monkeypatch=None,
    ):
        from app.agent.loop import AgentLoop, WorkingState

        self.loop = AgentLoop.__new__(AgentLoop)
        self.loop.mode = mode
        self.loop.context = {"research": [{"url": "https://x"}]} if grounded else {}
        self.loop.questions_declined = declined
        self.loop.working_state = WorkingState()
        self.loop.working_state.offered_deep_research = offered
        self.loop._session_has_documents = documents
        self.loop._settings_row = None
        self.llm = _FakeLLM(reply=verdict, raises=raises)
        self.loop.llm = self.llm

        if monkeypatch is not None:
            from app import ai_settings
            from app.agent import loop as loop_module

            config = (
                loop_module.ai_settings.SearchConfig(backend="tavily", api_key="test")
                if search_backend
                else None
            )
            monkeypatch.setattr(
                loop_module.ai_settings,
                "resolve_search_config",
                lambda row: (config, "" if config else "No deep-search backend is configured."),
            )
            assert ai_settings is loop_module.ai_settings

    async def should_offer(self, text="financial slides for starbucks"):
        return await self.loop._should_offer_deep_research(_user(text))

    def has_grounding(self):
        return self.loop._has_grounding()


@pytest.mark.asyncio
class TestOfferDecision:
    async def test_offers_when_the_judge_says_the_deck_needs_facts(self, monkeypatch):
        """
        The exact request that the previous keyword implementation missed:
        "financial" was not in the word list, so nothing was ever asked.
        """
        harness = _Loop(monkeypatch=monkeypatch)
        offer, figures = await harness.should_offer()
        assert offer is True
        assert figures == ["Starbucks FY2025 revenue"]

    async def test_stays_quiet_when_the_judge_says_no(self, monkeypatch):
        harness = _Loop(
            verdict='{"needs_research": false, "figures": []}', monkeypatch=monkeypatch
        )
        offer, _ = await harness.should_offer("a deck on how to run a retrospective")
        assert offer is False

    async def test_stays_quiet_when_no_search_backend_is_configured(self, monkeypatch):
        """
        Offering to research on a workspace with no search model set is
        offering something that cannot happen. The judge is not even asked.
        """
        harness = _Loop(search_backend=False, monkeypatch=monkeypatch)
        offer, _ = await harness.should_offer()
        assert offer is False
        assert harness.llm.calls == []

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"mode": MODE_DEEP},
            {"grounded": True},
            {"declined": True},
            {"offered": True},
            {"documents": True},
        ],
        ids=["deep-mode", "already-researched", "just-build-it", "already-asked", "has-upload"],
    )
    async def test_the_free_checks_come_first(self, kwargs, monkeypatch):
        """Each of these settles the question without spending an LLM call."""
        harness = _Loop(monkeypatch=monkeypatch, **kwargs)
        offer, _ = await harness.should_offer()
        assert offer is False
        assert harness.llm.calls == []

    async def test_stays_quiet_once_slides_exist(self, monkeypatch):
        """The offer changes where numbers come from; too late once they are on slides."""
        harness = _Loop(monkeypatch=monkeypatch)
        harness.loop.working_state.accumulated_slides = [{"slideIndex": 0}]
        offer, _ = await harness.should_offer()
        assert offer is False
        assert harness.llm.calls == []

    async def test_a_broken_judge_never_blocks_the_run(self, monkeypatch):
        harness = _Loop(raises=RuntimeError("provider down"), monkeypatch=monkeypatch)
        offer, figures = await harness.should_offer()
        assert offer is False
        assert figures == []

    async def test_an_unparseable_verdict_never_blocks_the_run(self, monkeypatch):
        harness = _Loop(verdict="I think probably yes?", monkeypatch=monkeypatch)
        offer, _ = await harness.should_offer()
        assert offer is False

    async def test_the_verdict_survives_prose_around_the_json(self, monkeypatch):
        harness = _Loop(
            verdict='Sure:\n```json\n{"needs_research": true, "figures": ["EV market size"]}\n```',
            monkeypatch=monkeypatch,
        )
        offer, figures = await harness.should_offer()
        assert offer is True
        assert figures == ["EV market size"]

    async def test_at_most_four_figures_are_carried(self, monkeypatch):
        harness = _Loop(
            verdict='{"needs_research": true, "figures": ["a","b","c","d","e","f"]}',
            monkeypatch=monkeypatch,
        )
        _, figures = await harness.should_offer()
        assert figures == ["a", "b", "c", "d"]


class TestTheQuestionItself:
    def _question(self, figures):
        from app.agent.loop import AgentLoop

        loop = AgentLoop.__new__(AgentLoop)
        return loop._deep_research_question(figures)

    def test_the_options_are_the_two_the_answer_matcher_knows(self):
        """
        The wording is matched verbatim on the way back. A model-authored
        variant would silently stop being recognised as an answer.
        """
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

    def test_the_singular_and_plural_shapes_both_carry_questions(self):
        """generate.py renders the 'questions' list; an empty one loses the offer."""
        question = self._question([])
        assert question["questions"][0]["options"] == [DEEP_RESEARCH_YES, DEEP_RESEARCH_NO]


class TestGroundingSignals:
    def test_an_analysed_document_is_grounding(self):
        harness = _Loop()
        harness.loop.working_state.record_completed_tool("analyze_document", {}, {})
        assert harness.has_grounding() is True

    def test_researched_urls_are_grounding(self):
        harness = _Loop()
        harness.loop.working_state.researched_urls.add("https://example.com")
        assert harness.has_grounding() is True

    def test_the_models_own_memory_is_not_grounding(self):
        """The whole point: memory is what produced the invented $40.0B."""
        assert _Loop().has_grounding() is False


class TestCheckpointsWithholdToolsInsteadOfRefusingThem:
    """
    The user-visible half of #77: the agent "tried skipping questions and then
    threw an error on themselves". A refused tool call is rendered as a failed
    step. Withholding the schema is the same rule one step earlier, where it
    produces no error at all.
    """

    def _loop(self, consulted=False, reviewed=False, declined=False):
        from app.agent.loop import AgentLoop, WorkingState

        loop = AgentLoop.__new__(AgentLoop)
        loop.questions_declined = declined
        loop.working_state = WorkingState()
        loop.working_state.consulted_before_planning = consulted
        loop.working_state.reviewed_outline_with_user = reviewed
        return loop

    def test_nothing_that_writes_a_deck_is_offered_before_the_first_question(self):
        withheld = self._loop()._tools_withheld_by_checkpoint()
        assert PLANNING_TOOLS <= withheld
        assert DRAFTING_TOOLS <= withheld

    def test_planning_opens_up_once_the_user_has_been_consulted(self):
        withheld = self._loop(consulted=True)._tools_withheld_by_checkpoint()
        assert not (PLANNING_TOOLS & withheld)
        assert DRAFTING_TOOLS <= withheld

    def test_everything_opens_up_once_the_outline_is_agreed(self):
        withheld = self._loop(consulted=True, reviewed=True)._tools_withheld_by_checkpoint()
        assert withheld == set()

    def test_just_build_it_lifts_both_checkpoints(self):
        assert self._loop(declined=True)._tools_withheld_by_checkpoint() == set()

    def test_ask_user_question_is_never_withheld(self):
        """It is the only way out of a checkpoint."""
        assert "ask_user_question" not in self._loop()._tools_withheld_by_checkpoint()

    def test_the_reading_tools_are_never_withheld(self):
        withheld = self._loop()._tools_withheld_by_checkpoint()
        for tool in ("analyze_document", "extract_key_data", "web_search"):
            assert tool not in withheld


class _FakeRedis:
    """Just enough of the Redis client for the working-state round trip."""

    def __init__(self):
        self.store: dict = {}

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, key):
        self.store.pop(key, None)


async def _async(value):
    return value


class _Run:
    """A full AgentLoop.run pass with only the database and provider stubbed.

    ``replies`` is consumed one per LLM call. The first call of a simple-mode
    run is the grounding judge, so a test can both feed it a verdict and prove
    what the loop did or did not do next. Once the replies run out the stub
    answers without tool calls and the run winds down at the iteration cap;
    ``strict`` turns that into a failure instead, for the tests counting calls.

    ``consulted`` seeds the two consultation checkpoints as already met, via
    the same Redis working state a resumed run would restore. Without it the
    deck-writing tools are withheld and no planning could be observed.
    """

    def __init__(
        self,
        monkeypatch,
        chat_messages,
        replies,
        search_backend=True,
        consulted=False,
        strict=False,
    ):
        from app.agent import loop as loop_module
        from app.agent.loop import MODE_SIMPLE, AgentLoop
        from app.agent.registry import ToolResult

        self.session_id = uuid.uuid4()
        self.replies = list(replies)
        self.sent: list = []
        self.executed: list[str] = []

        self.loop = AgentLoop(db=None, mode=MODE_SIMPLE)
        self.loop.redis_client = _FakeRedis()
        if consulted:
            self.loop.redis_client.store[f"working_state:{self.session_id}"] = json.dumps({
                "consulted_before_planning": True,
                "reviewed_outline_with_user": True,
            })

        monkeypatch.setattr(self.loop, "_load_context", lambda *a, **k: _async({}))
        monkeypatch.setattr(
            self.loop, "_build_chat_messages", lambda *a, **k: _async(chat_messages)
        )

        config = (
            loop_module.ai_settings.SearchConfig(backend="tavily", api_key="test")
            if search_backend
            else None
        )
        monkeypatch.setattr(
            loop_module.ai_settings,
            "resolve_search_config",
            lambda row: (config, "" if config else "No deep-search backend is configured."),
        )

        async def _chat(messages, tools=None):
            self.sent.append({"messages": messages, "tools": tools})
            if self.replies:
                return self.replies.pop(0)
            if strict:
                raise AssertionError("the loop made more LLM calls than the test scripted")
            return {"message": {"content": "that is all"}, "tool_calls": []}

        async def _execute_tool(name, args):
            self.executed.append(name)
            return ToolResult(success=True, data={})

        monkeypatch.setattr(self.loop, "_chat_with_retry", _chat)
        monkeypatch.setattr(self.loop, "_execute_tool", _execute_tool)

    async def events(self) -> list[dict]:
        return [ev async for ev in self.loop.run(self.session_id)]


def _verdict(needs, figures=()):
    return {
        "message": {
            "content": json.dumps({"needs_research": needs, "figures": list(figures)})
        },
        "tool_calls": [],
    }


def _calls_plan():
    return {
        "message": {"content": "Planning the deck."},
        "tool_calls": [{
            "id": "call_1",
            "function": {"name": "plan_presentation_structure", "arguments": "{}"},
        }],
    }


@pytest.mark.asyncio
class TestTheRunActuallyStopsAndAsks:
    """
    End to end through AgentLoop.run, which is where #77 was reported from:
    "it did in fact not ask me for websearch when in simple mode asking for
    financial slides for starbucks".
    """

    async def test_a_factual_simple_run_asks_before_running_any_tool(self, monkeypatch):
        run = _Run(
            monkeypatch,
            [{"role": "user", "content": "financial slides for starbucks"}],
            # Only the judge should ever be called. If the loop carried on into
            # the ReAct loop it would call the LLM a second time, and strict
            # mode turns that into a failure.
            [_verdict(True, ["Starbucks FY2025 revenue"])],
            strict=True,
        )

        events = await run.events()
        types = [e["type"] for e in events]

        assert "question" in types
        assert types[-1] == "done"
        assert events[-1]["paused"] is True
        # The whole complaint: no tool was attempted, so nothing was refused,
        # so no error was shown where a question belonged.
        assert run.executed == []
        assert "error" not in types
        assert len(run.sent) == 1

    async def test_the_question_carries_the_two_matchable_options(self, monkeypatch):
        run = _Run(
            monkeypatch,
            [{"role": "user", "content": "financial slides for starbucks"}],
            [_verdict(True, ["Starbucks FY2025 revenue"])],
            strict=True,
        )

        question = next(e for e in await run.events() if e["type"] == "question")

        assert question["options"] == [DEEP_RESEARCH_YES, DEEP_RESEARCH_NO]
        assert "Starbucks FY2025 revenue" in question["context"]

    async def test_the_pause_is_recorded_so_the_resume_does_not_ask_again(self, monkeypatch):
        run = _Run(
            monkeypatch,
            [{"role": "user", "content": "financial slides for starbucks"}],
            [_verdict(True)],
            strict=True,
        )

        await run.events()
        saved = json.loads(run.loop.redis_client.store[f"working_state:{run.session_id}"])

        assert saved["offered_deep_research"] is True
        assert run.loop.working_state.is_paused_for_user is True

    async def test_a_conceptual_request_is_not_interrupted(self, monkeypatch):
        run = _Run(
            monkeypatch,
            [{"role": "user", "content": "a deck on how to run a retrospective"}],
            [_verdict(False), _calls_plan()],
            consulted=True,
        )

        events = await run.events()

        assert "question" not in [e["type"] for e in events]
        # It got on with the deck rather than stopping to ask.
        assert run.executed and run.executed[0] == "plan_presentation_structure"

    async def test_answering_yes_switches_the_run_to_deep_research(self, monkeypatch):
        run = _Run(
            monkeypatch,
            [
                {"role": "user", "content": "financial slides for starbucks"},
                {"role": "assistant", "content": "Research them on the web?"},
                {"role": "user", "content": DEEP_RESEARCH_YES},
            ],
            [_calls_plan()],
            consulted=True,
        )

        events = await run.events()

        assert run.loop.mode == MODE_DEEP
        assert any(
            "Deep research enabled" in (e.get("content") or "")
            for e in events
            if e["type"] == "thought"
        )
        # And it does not ask the same thing twice.
        assert "question" not in [e["type"] for e in events]

    async def test_answering_no_gets_on_with_it(self, monkeypatch):
        run = _Run(
            monkeypatch,
            [
                {"role": "user", "content": "financial slides for starbucks"},
                {"role": "assistant", "content": "Research them on the web?"},
                {"role": "user", "content": DEEP_RESEARCH_NO},
            ],
            [_calls_plan()],
            consulted=True,
        )

        events = await run.events()

        assert run.loop.mode == MODE_SIMPLE
        assert "question" not in [e["type"] for e in events]
        assert run.executed and run.executed[0] == "plan_presentation_structure"

    async def test_no_search_backend_means_no_offer_and_no_judge_call(self, monkeypatch):
        """Nothing to switch on, so nothing to ask about."""
        run = _Run(
            monkeypatch,
            [{"role": "user", "content": "financial slides for starbucks"}],
            [_calls_plan()],
            search_backend=False,
            consulted=True,
        )

        events = await run.events()

        assert "question" not in [e["type"] for e in events]
        # The first LLM call was the agent, not the judge: it was given tools.
        assert run.sent[0]["tools"]
        assert run.executed and run.executed[0] == "plan_presentation_structure"


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
