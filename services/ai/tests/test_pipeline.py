"""
The run has a shape, and the loop is what holds it.

Every step used to be the model's choice: it was handed whatever tools were not
currently withheld and left to work out the rest. It guessed - three question
pauses in a row, a design system re-extracted three times, ten searches, six
document tools called against a session with no documents.

Now the sequence is data (app/agent/pipeline.py) and the loop walks it. These
tests drive whole runs with a scripted model and assert on the order things
actually happen in, because that order is the feature.
"""

import json
import uuid

import pytest

from app.agent import pipeline
from app.agent.loop import AgentLoop
from app.agent.registry import ToolResult

INTAKE = json.dumps({
    "brief": "Five slides on German native dog breeds, for breeders.",
    "audience": "dog breeders",
    "slideCount": 5,
    "wantsAutonomy": True,
    "needsResearch": True,
    "figures": ["number of recognised breeds"],
    "searchQueries": ["german native dog breeds", "german breed registry numbers"],
    "ambiguities": [
        {"question": "Which breeds should it cover?",
         "options": ["All 32 recognised", "The best known six"]},
    ],
})


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


def _text(content):
    return {"message": {"content": content}, "tool_calls": []}


def _calls(*names_and_args):
    return {
        "message": {"content": ""},
        "tool_calls": [
            {"id": f"c{i}", "function": {"name": name, "arguments": json.dumps(args)}}
            for i, (name, args) in enumerate(names_and_args)
        ],
    }


TOOL_RESULTS = {
    "extract_template_design_system": {"palette": {"primary": "#1F4E78"},
                                       "layouts": [{"layoutId": "content"}]},
    "web_search": {"query": "q", "results": [
        {"title": "t", "url": "https://example.com/a", "snippet": "s"},
        {"title": "u", "url": "https://example.com/b", "snippet": "s"},
    ]},
    "ask_user_question": None,   # filled from the args, like the real tool
    "propose_design_directions": {
        "question": "Which design direction should the deck follow?",
        "options": ["Dark analyst"], "questions": [], "context": "",
        "directions": [{"id": "dark-analyst", "name": "Dark analyst",
                        "designSystem": {"marker": "dark"}}],
    },
    "plan_presentation_structure": {
        "title": "German Native Dog Breeds",
        "totalSlides": 2,
        "slides": [{"title": "Origins"}, {"title": "The breeds today"}],
    },
    "select_layout": {"layoutId": "content"},
    "design_slide_content": None,  # filled per call
    "finalize_and_render_pptx": {"filePath": "/tmp/deck.pptx", "previewUrls": []},
    "validate_rendered_output": {"issues": []},
}


def _loop(script, *, mode="deep", state=None, messages=(), monkeypatch=None):
    """A loop whose model is a fixed list of replies and whose tools all work."""
    session_id = uuid.uuid4()
    loop = AgentLoop(db=None, mode=mode, detail_level="detailed")
    loop.redis_client = _FakeRedis(
        {f"working_state:{session_id}": json.dumps(state)} if state else None
    )
    loop.search_config = object()          # a backend exists
    loop._session_has_documents = False

    replies = list(script)
    seen: list[str] = []

    async def _chat(messages=None, tools=None):
        seen.append("model")
        return replies.pop(0) if replies else _text("done")

    async def _execute(name, args):
        seen.append(name)
        if name == "ask_user_question":
            questions = args.get("questions") or []
            return ToolResult(success=True, data={
                "questions": questions,
                "question": questions[0]["question"] if questions else "",
                "options": questions[0]["options"] if questions else [],
                "context": args.get("context", ""),
                "type": "user_question",
            })
        if name == "design_slide_content":
            return ToolResult(success=True, data={
                "slideIndex": args.get("slide_index"), "title": "t", "contents": [],
            })
        return ToolResult(success=True, data=dict(TOOL_RESULTS.get(name) or {}))

    monkeypatch.setattr(loop, "_chat_with_retry", _chat)
    monkeypatch.setattr(loop, "_execute_tool", _execute)
    monkeypatch.setattr(loop, "_load_context", lambda *a, **k: _async({}))
    monkeypatch.setattr(loop, "_build_chat_messages", lambda *a, **k: _async(list(messages)))
    monkeypatch.setattr(loop, "_configure", lambda: _async(None))
    return loop, session_id, seen


class TestTheOrderIsFixed:
    @pytest.mark.asyncio
    async def test_a_fresh_run_understands_then_scouts_then_asks(self, monkeypatch):
        """Intake first, always. The searches that follow are the small ones
        that make the questions concrete - not the deck's research, which waits
        until the answers have narrowed the topic."""
        loop, session_id, seen = _loop(
            [_text(INTAKE)],
            messages=[{"role": "user", "content": "5 slides on german dog breeds"}],
            monkeypatch=monkeypatch,
        )

        events = [e async for e in loop.run(session_id)]

        assert seen == [
            "model",                     # intake, before anything acts
            "web_search", "web_search",  # the scout, capped at two
            "ask_user_question",         # the one consultation pause
        ]
        assert [e["type"] for e in events][-2:] == ["question", "done"]
        assert events[-1]["paused"] is True

    @pytest.mark.asyncio
    async def test_a_hands_off_request_is_offered_the_skip_on_its_own(self, monkeypatch):
        """The offer and the questions it would cancel are not asked together:
        that asks the user to answer both sides of one decision. The offer goes
        first, alone, and the questions wait to see whether it is taken."""
        loop, session_id, _ = _loop(
            [_text(INTAKE)], messages=[{"role": "user", "content": "x"}],
            monkeypatch=monkeypatch,
        )

        events = [e async for e in loop.run(session_id)]

        question = next(e for e in events if e["type"] == "question")
        asked = [q["question"] for q in question["questions"]]
        assert asked == ["Shall I decide the details myself and build it?"]
        # Not the consultation itself - declining it leaves every real question
        # still to ask.
        assert loop.working_state.consulted_before_planning is False
        assert loop.working_state.autonomy_asked is True

    @pytest.mark.asyncio
    async def test_a_request_with_nothing_open_is_still_consulted(self, monkeypatch):
        """Consultation is the default and stays the default. It is not
        skipped because a model could not think of a question - only because
        the user said to skip it."""
        intake = json.loads(INTAKE)
        intake["ambiguities"] = []
        intake["needsResearch"] = False
        intake["wantsAutonomy"] = False
        loop, session_id, seen = _loop(
            [_text(json.dumps(intake))],
            messages=[{"role": "user", "content": "x"}], monkeypatch=monkeypatch,
        )

        events = [e async for e in loop.run(session_id)]

        assert "ask_user_question" in seen
        question = next(e for e in events if e["type"] == "question")
        assert question["question"] == "How should I pitch this?"
        assert loop.working_state.consulted_before_planning is True

    @pytest.mark.asyncio
    async def test_but_a_genuine_ambiguity_still_stops_it(self, monkeypatch):
        intake = json.loads(INTAKE)
        intake["wantsAutonomy"] = False
        intake["needsResearch"] = False
        loop, session_id, _ = _loop(
            [_text(json.dumps(intake))],
            messages=[{"role": "user", "content": "x"}], monkeypatch=monkeypatch,
        )

        events = [e async for e in loop.run(session_id)]

        question = next(e for e in events if e["type"] == "question")
        asked = [q["question"] for q in question["questions"]]
        # The clarifying question, and not the handover one the user never
        # implied.
        assert asked == ["Which breeds should it cover?"]


class TestTheRunResumesWhereItStopped:
    @staticmethod
    def _after_consult(**overrides):
        state = {
            "brief": json.loads(INTAKE),
            "scouted": True,
            "consulted_before_planning": True,
            "design_system": {"palette": {}, "layouts": []},
            "mode": "deep",
            "detail_level": "detailed",
        }
        state.update(overrides)
        return state

    @pytest.mark.asyncio
    async def test_answering_the_questions_leads_to_the_design_choice(self, monkeypatch):
        loop, session_id, seen = _loop(
            [], state=self._after_consult(),
            messages=[{"role": "user", "content": "All 32 recognised"}],
            monkeypatch=monkeypatch,
        )

        events = [e async for e in loop.run(session_id)]

        assert seen == ["propose_design_directions"]
        question = next(e for e in events if e["type"] == "question")
        assert question["question"] == "Which design direction should the deck follow?"

    @pytest.mark.asyncio
    async def test_taking_the_skip_ends_every_pause_and_settles_the_design(
        self, monkeypatch
    ):
        """A confirmed handover is the one thing that turns the consultation
        off - all of it, including the outline review. The deck still gets a
        design: the first direction, which is what the user would have been
        offered first anyway, rather than whatever the default happened to be.
        """
        loop, session_id, seen = _loop(
            [_calls(("web_search", {"query": "german breeds registry"})),
             _calls(("select_layout", {"slide_index": 0}),
                    ("design_slide_content", {"slide_index": 0})),
             _calls(("select_layout", {"slide_index": 1}),
                    ("design_slide_content", {"slide_index": 1})),
             _text("Built you a deck on German dog breeds.")],
            state=self._after_consult(
                autonomy_asked=True,
                web_search_calls=9,
                researched_urls=[f"https://s/{i}" for i in range(8)],
            ),
            messages=[{"role": "user",
                       "content": "Yes - decide audience, focus and design, and go ahead"}],
            monkeypatch=monkeypatch,
        )

        events = [e async for e in loop.run(session_id)]

        assert loop.working_state.autonomy_confirmed is True
        assert "propose_design_directions" not in seen
        # Nothing was put to the user at all - not the design, not the outline.
        assert not [e for e in events if e["type"] == "question"]
        assert "finalize_and_render_pptx" in seen

        # And it is building in a real direction, not on the bare default.
        chosen = loop.working_state.chosen_direction
        assert chosen and chosen["id"] == "clean-minimal"
        assert loop.context["design_system"]["grounds"]["cover"]

    @pytest.mark.asyncio
    async def test_approving_the_outline_builds_renders_and_validates(self, monkeypatch):
        """The rest of the run has no pauses left in it: draft each slide in
        order, render, check the file."""
        loop, session_id, seen = _loop(
            [_calls(("select_layout", {"slide_index": 0}),
                    ("design_slide_content", {"slide_index": 0})),
             _calls(("select_layout", {"slide_index": 1}),
                    ("design_slide_content", {"slide_index": 1})),
             _text("Built you a two-slide deck on German dog breeds.")],
            state=self._after_consult(
                autonomy_confirmed=True,
                reviewed_outline_with_user=True,
                presentation_structure=TOOL_RESULTS["plan_presentation_structure"],
                web_search_calls=9,
                researched_urls=[f"https://s/{i}" for i in range(8)],
            ),
            messages=[{"role": "user", "content": "Go ahead as planned"}],
            monkeypatch=monkeypatch,
        )

        events = [e async for e in loop.run(session_id)]

        assert seen.index("design_slide_content") < seen.index("finalize_and_render_pptx")
        assert seen.index("finalize_and_render_pptx") < seen.index("validate_rendered_output")
        assert seen.count("design_slide_content") == 2
        assert not [e for e in events if e["type"] == "error"]
        # The closing word is the model's, and it is the last thing said.
        assert events[-1]["type"] == "thought"
        assert "German dog breeds" in events[-1]["content"]

    @pytest.mark.asyncio
    async def test_the_outline_is_shown_to_everyone_who_did_not_take_the_skip(
        self, monkeypatch
    ):
        """Seeing the slide titles before they are written is the cheapest
        moment to redirect a deck, so it survives everything short of the user
        saying they do not want to be asked."""
        loop, session_id, seen = _loop(
            [_calls(("web_search", {"query": "q"}))],
            state=self._after_consult(
                offered_directions=True,
                web_search_calls=9,
                researched_urls=[f"https://s/{i}" for i in range(8)],
            ),
            messages=[{"role": "user", "content": "All 32 recognised"}],
            monkeypatch=monkeypatch,
        )

        events = [e async for e in loop.run(session_id)]

        question = next(e for e in events if e["type"] == "question")
        assert question["question"] == "Shall I build this outline?"
        assert "1. Origins" in question["context"]


class TestRevisionWalksBackwards:
    def test_a_flagged_slide_makes_drafting_unfinished_again(self):
        loop = AgentLoop(db=None, mode="simple")
        loop.questions_declined = True
        ws = loop.working_state
        ws.brief = {"needsResearch": False}
        ws.offered_directions = True
        ws.presentation_structure = {"totalSlides": 2, "slides": [{}, {}]}
        ws.accumulated_slides = [{"slideIndex": 0}, {"slideIndex": 1}]
        ws.has_called_finalize = True
        ws.rendered_serial = ws.design_serial
        ws.validation_clean = True

        assert pipeline.next_phase(loop) is None

        # Validation comes back with a problem on slide 2.
        ws.validation_clean = False
        ws.validation_issues = [{"slideIndex": 1, "type": "overflow"}]
        ws.slides_needing_revision = [1]
        ws.validated_serial = ws.design_serial

        assert pipeline.next_phase(loop).name == "draft"

        # Rewriting it settles the finding: what is outstanding now is a fresh
        # render, not another go at the same slide.
        ws.add_slide({"slideIndex": 1, "title": "shorter"})

        assert pipeline.next_phase(loop).name == "render"

    def test_and_a_rewritten_slide_makes_the_rendered_file_stale(self):
        loop = AgentLoop(db=None, mode="simple")
        loop.questions_declined = True
        ws = loop.working_state
        ws.brief = {"needsResearch": False}
        ws.offered_directions = True
        ws.presentation_structure = {"totalSlides": 1, "slides": [{}]}
        ws.add_slide({"slideIndex": 0})
        ws.has_called_finalize = True
        ws.rendered_serial = ws.design_serial

        assert ws.render_is_current

        ws.add_slide({"slideIndex": 0, "title": "revised"})

        assert not ws.render_is_current
        assert pipeline.next_phase(loop).name == "render"


class TestTheLoopCallsToolsWithArgumentsTheyHave:
    """The loop is the caller now, so a parameter-name mismatch is not a model
    that tries again differently - it is a stalled run. One shipped: the
    planner was called with "topic" and "slide_count" when it takes
    "content_summary" and "target_slide_count", and the run died at the plan
    step having done all its research."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("phase_name", [
        "scout", "consult", "design", "plan", "review", "render", "validate",
    ])
    async def test_every_loop_driven_phase_passes_arguments_the_tool_accepts(
        self, phase_name, monkeypatch
    ):
        import inspect

        from app.agent.registry import get_registry

        registry = get_registry()
        loop, session_id, _ = _loop(
            [], state={
                "brief": json.loads(INTAKE),
                "scouted": phase_name != "scout",
                "consulted_before_planning": phase_name not in ("scout", "consult"),
                "offered_directions": phase_name not in ("scout", "consult", "design"),
                "presentation_structure": TOOL_RESULTS["plan_presentation_structure"],
                # The planner refuses to replan a deck that has slides, so the
                # one phase that must run without them gets none.
                "accumulated_slides": (
                    [] if phase_name == "plan" else [{"slideIndex": 0}, {"slideIndex": 1}]
                ),
                "has_called_finalize": True,
                "last_rendered_path": "/tmp/deck.pptx",
                "design_system": {"palette": {}, "layouts": []},
                "mode": "deep",
            },
            monkeypatch=monkeypatch,
        )
        await loop._load_working_state(session_id)
        loop.context = {"design_system": {"palette": {}, "layouts": []}}

        checked = []

        async def _check(name, args):
            checked.append(name)
            entry = registry.get(name)
            assert entry, f"unknown tool {name}"
            # Raises TypeError if the loop passed something the tool has no
            # parameter for - which is the whole point of this test.
            inspect.signature(entry[1]).bind(**args)
            return await _tool_stub(name, args)

        async def _tool_stub(name, args):
            if name == "ask_user_question":
                questions = args.get("questions") or []
                return ToolResult(success=True, data={
                    "questions": questions,
                    "question": questions[0]["question"] if questions else "",
                    "options": questions[0]["options"] if questions else [],
                    "context": args.get("context", ""),
                })
            return ToolResult(success=True, data=dict(TOOL_RESULTS.get(name) or {}))

        monkeypatch.setattr(loop, "_execute_tool", _check)

        handler = getattr(loop, f"_phase_{phase_name}")
        async for _ in handler(loop.context, [], session_id):
            pass

        assert checked, f"the {phase_name} phase called no tool"


class TestTheAutonomyAnswerIsReadCorrectly:
    """The reply arrives with the question echoed in front of it, and the
    question is full of the words a "yes" is matched on. A yes-first matcher
    read every refusal as consent - so a user who asked to be involved was
    skipped past the design choice entirely."""

    @staticmethod
    def _said(text):
        from app.agent.loop import user_confirmed_autonomy

        return user_confirmed_autonomy([{"role": "user", "content": text}])

    def test_the_offered_yes_is_a_yes(self):
        assert self._said(
            "Shall I decide the details myself and build it? -> "
            "Yes - decide audience, focus and design, and go ahead"
        ) is True

    def test_the_offered_no_is_a_no(self):
        assert self._said(
            "Shall I decide the details myself and build it? -> "
            "No - I'll answer a few questions first"
        ) is False

    def test_a_bare_question_echo_is_not_consent(self):
        assert self._said("Shall I decide the details myself and build it?") is False

    def test_a_typed_handover_still_counts(self):
        assert self._said("you decide, I don't mind") is True

    def test_and_a_typed_refusal_still_counts(self):
        assert self._said("no, ask me the questions") is False

    def test_the_latest_word_wins(self):
        from app.agent.loop import user_confirmed_autonomy

        assert user_confirmed_autonomy([
            {"role": "user", "content": "you decide"},
            {"role": "user", "content": "actually, ask me first"},
        ]) is False
