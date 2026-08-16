"""
The run advances instead of stalling.

Every defect here comes from one observed session. The user asked for five
slides on German dog breeds and was asked three separate questions, in three
separate pauses, none of which was the design-direction choice the run is
built around; between them the agent re-extracted the design system it already
had, ran ten web searches, and called extract_key_data six times against a run
with no documents in it, each one reporting that it had found nothing.

None of that was the model being stupid. Every step of it was the loop's own
doing:

* the tool that carries the design choice was withheld from the schema, absent
  from the prompt's tool list, and absent from its workflow - the model was
  never told it existed, let alone that the run was waiting on it;
* the prompt simultaneously *ordered* the model to ask about visual style in
  words, which is the question the directions replaced;
* nothing bounded ask_user_question, so "ask again" was always an available
  move and always looked like progress;
* extract_key_data's JSON parse could not survive a fenced response, so a
  successful extraction was reported to the user as an empty one.
"""

import json
import uuid

import pytest

from app.agent.registry import ToolResult


def _loop(**state):
    """An AgentLoop with just enough on it to answer questions about sequencing."""
    from app.agent.loop import AgentLoop, WorkingState

    loop = AgentLoop.__new__(AgentLoop)
    loop.questions_declined = False
    loop.mode = "deep"
    loop.detail_level = "detailed"
    loop.working_state = WorkingState()
    loop.context = {}
    loop._session_has_documents = False
    loop.search_config = object()
    loop.working_state.brief = {"needsResearch": True, "ambiguities": []}
    for key, value in state.items():
        if hasattr(loop.working_state, key):
            setattr(loop.working_state, key, value)
        else:
            setattr(loop, key, value)
    return loop


class TestThePromptDescribesTheRunTheLoopEnforces:
    def test_the_direction_choice_is_in_the_tool_list(self):
        """A tool the model is never told about is a tool it never calls."""
        prompt = _loop()._build_system_prompt({})

        assert "propose_design_directions" in prompt

    def test_the_prompt_no_longer_narrates_a_workflow_the_model_picks_from(self):
        """A list of steps is an invitation to be on a different one. The only
        step in the prompt now is the step the run is actually on."""
        prompt = _loop()._build_system_prompt({})

        assert "## Workflow" not in prompt
        assert "## Next Step" in prompt

    def test_style_is_not_asked_for_in_words(self):
        """The three rendered directions ARE the style question. Asking it in
        prose as well is how a run spends a pause on 'professional, warm or
        bold' and then never offers the directions at all."""
        prompt = _loop()._build_system_prompt({}).lower()

        assert "visual style / color" not in prompt

    def test_the_prompt_says_what_this_run_does_next(self):
        """The pipeline scopes the tools, so 'what can I call' no longer
        implies 'what should I do'. Left to infer it, the model re-reads
        things it has already read."""
        loop = _loop(consulted_before_planning=True)
        loop.mode = "simple"

        prompt = loop._build_system_prompt({})

        assert "## Next Step" in prompt
        assert "design direction" in prompt.split("## Next Step", 1)[1]

    def test_a_deep_run_scouts_before_it_asks(self):
        """The questions are only worth asking once there is material to draw
        their options from."""
        assert "Look up enough" in _loop()._next_step()

    def test_and_consults_once_it_has_looked_around(self):
        loop = _loop(scouted=True)

        assert "questions to the user" in loop._next_step()


class TestOnePausePerCheckpoint:
    """The observed run's second and third pauses: both were allowed, both
    cost the user a round trip, and neither could advance anything. A question
    is no longer something the model can decide to ask instead of working - the
    pauses belong to phases, and each phase happens once."""

    def test_the_consultation_is_one_phase_that_happens_once(self):
        from app.agent import pipeline

        loop = _loop(scouted=True)
        assert pipeline.next_phase(loop).name == "consult"

        loop.working_state.consulted_before_planning = True
        assert pipeline.next_phase(loop).name != "consult"

    def test_and_the_design_choice_is_the_next_thing_after_it(self):
        from app.agent import pipeline

        loop = _loop(scouted=True, consulted_before_planning=True)

        assert pipeline.next_phase(loop).name == "design"


class TestNothingIsReadTwice:
    def test_key_data_needs_material_to_extract_from(self):
        """Six calls, 39 seconds, no documents in the run at all."""
        refusal = _loop()._check_precondition("extract_key_data", {})

        assert refusal is not None

    def test_key_data_is_allowed_once_a_document_is_read(self):
        loop = _loop(_session_has_documents=True)

        assert loop._check_precondition("extract_key_data", {}) is None


class TestASearchIsNotRunTwice:
    RESEARCH = [
        {"query": "German native dog breeds", "title": "Breeds", "url": "https://a",
         "snippet": "32 breeds"},
        {"query": "German shepherd history", "title": "GSD", "url": "https://b",
         "snippet": "1899"},
    ]

    def test_a_repeat_is_answered_from_the_findings(self):
        cached = _loop()._answer_from_context(
            "web_search",
            {"query": "  german native dog BREEDS?  "},
            {"research": self.RESEARCH},
        )

        assert cached is not None
        assert [r["url"] for r in cached.data["results"]] == ["https://a"]

    def test_a_genuinely_new_question_still_runs(self):
        assert _loop()._answer_from_context(
            "web_search",
            {"query": "German dog breed registration numbers 2026"},
            {"research": self.RESEARCH},
        ) is None


class TestTheChosenDirectionSurvives:
    def test_re_extraction_is_answered_from_what_the_run_holds(self):
        """context["design_system"] is what the planner, the layouts, the copy
        and the renderer all read. A re-extraction landing on top of it throws
        away the direction the user picked, silently."""
        chosen = {"sourceType": "direction", "marker": "dark-analyst"}
        loop = _loop(chosen_direction={"id": "dark-analyst"})

        cached = loop._answer_from_context(
            "extract_template_design_system", {}, {"design_system": chosen}
        )

        assert cached is not None and cached.success
        assert cached.data["marker"] == "dark-analyst"

    def test_a_default_system_is_not_re_extracted_either(self):
        loop = _loop()

        cached = loop._answer_from_context(
            "extract_template_design_system", {}, {"design_system": {"palette": {}}}
        )

        assert cached is not None

    def test_a_run_with_no_design_system_yet_extracts_normally(self):
        assert _loop()._answer_from_context(
            "extract_template_design_system", {}, {}
        ) is None

    def test_a_new_colour_can_still_be_applied_before_a_direction_is_picked(self):
        loop = _loop()

        assert loop._answer_from_context(
            "extract_template_design_system",
            {"color_theme": "#1F4E78"},
            {"design_system": {"palette": {}}},
        ) is None

    def test_but_never_over_a_chosen_direction(self):
        loop = _loop(chosen_direction={"id": "dark-analyst"})

        assert loop._answer_from_context(
            "extract_template_design_system",
            {"color_theme": "#1F4E78"},
            {"design_system": {"sourceType": "direction"}},
        ) is not None


class TestASuccessfulExtractionIsReportedAsOne:
    @pytest.mark.asyncio
    async def test_fenced_json_is_read(self, monkeypatch):
        """Models fence their JSON. A bare json.loads does not survive it, and
        the fallback quietly produced an object with none of the expected keys -
        which the activity feed then summarised, six times over, as 'Found no
        structured data points in the material.'"""
        from app.agent.tools import extract_key_data as module

        payload = {
            "metrics": [{"name": "Breeds", "value": 32, "unit": "count"}],
            "key_points": ["Germany recognises 32 native breeds"],
            "dates": [],
            "names": [],
            "figures": [],
            "topics": ["dog breeds"],
        }

        class FencedLLM:
            async def chat(self, messages, tools=None):
                return {
                    "message": {
                        "content": "Here you go:\n```json\n"
                        + json.dumps(payload)
                        + "\n```"
                    }
                }

        monkeypatch.setattr(module, "require_chat_provider", lambda: FencedLLM())

        result = await module.ExtractKeyDataTool.run(content="anything")

        assert result.success
        assert result.data["metrics"][0]["name"] == "Breeds"

    @pytest.mark.asyncio
    async def test_an_unreadable_response_fails_loudly(self, monkeypatch):
        from app.agent.tools import extract_key_data as module

        class ProseLLM:
            async def chat(self, messages, tools=None):
                return {"message": {"content": "I could not find anything useful."}}

        monkeypatch.setattr(module, "require_chat_provider", lambda: ProseLLM())

        result = await module.ExtractKeyDataTool.run(content="anything")

        assert not result.success


class _FakeRedis:
    def __init__(self, initial=None):
        self.store = dict(initial or {})

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, key):
        self.store.pop(key, None)


async def _async(value):
    return value


def _call(name, **args):
    return {
        "message": {"content": ""},
        "tool_calls": [
            {"id": name, "function": {"name": name, "arguments": json.dumps(args)}}
        ],
    }


class TestTheSecondTurnOfTheReportedSession:
    """The run resumes with the consultation answered.

    The reported session did what it liked here: re-read the design system it
    already had, then asked about visual style - a pause that settled nothing,
    because the three rendered directions are that question. Both were legal
    moves for a model that was being asked what to do next. It is not asked any
    more: the phase after the consultation is the design choice, and that is
    the only thing that happens.
    """

    @staticmethod
    def _resumed_state():
        return json.dumps({
            "brief": {"brief": "German dog breeds, for breeders",
                      "needsResearch": False, "ambiguities": []},
            "scouted": True,
            "consulted_before_planning": True,
            "answered_questions": [{"question": "Audience?", "answer": "Breeders"}],
            "design_system": {"sourceType": "default", "layouts": [{"name": "Title"}]},
            "mode": "simple",
            "detail_level": "detailed",
        })

    @pytest.mark.asyncio
    async def test_it_goes_straight_to_the_design_directions(self, monkeypatch):
        from app.agent.loop import AgentLoop

        session_id = uuid.uuid4()
        loop = AgentLoop(db=None, mode="simple", detail_level="detailed")
        loop.redis_client = _FakeRedis(
            {f"working_state:{session_id}": self._resumed_state()}
        )
        monkeypatch.setattr(loop, "_load_context", lambda *a, **k: _async({}))
        monkeypatch.setattr(loop, "_build_chat_messages", lambda *a, **k: _async([]))

        executed = []

        async def _chat(*args, **kwargs):
            raise AssertionError(
                "the model is not consulted about what happens next here"
            )

        async def _execute(name, args):
            executed.append(name)
            return ToolResult(success=True, data={
                "question": "Which design direction should the deck follow?",
                "options": ["Dark analyst"],
                "questions": [],
                "context": "",
                "directions": [{"id": "dark-analyst", "name": "Dark analyst"}],
            })

        monkeypatch.setattr(loop, "_chat_with_retry", _chat)
        monkeypatch.setattr(loop, "_execute_tool", _execute)

        events = [e async for e in loop.run(session_id)]

        # No re-extraction, no second question - one call, and it is the one
        # the user was waiting for.
        assert executed == ["propose_design_directions"]
        question = next(e for e in events if e["type"] == "question")
        assert question["question"] == "Which design direction should the deck follow?"
        assert loop.working_state.offered_directions
        assert events[-1]["paused"] is True
