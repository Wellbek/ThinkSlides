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
from app.agent.loop import AgentLoop, WorkingState
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
        # The format question is reserved ahead of everything else and always
        # fires first. With nothing else to ask, the pitch-depth fallback
        # rides along as the second question rather than being dropped -
        # consultation is not skipped for want of a question, only because
        # the user said to skip it.
        assert question["question"] == "What kind of presentation is this?"
        asked = [q["question"] for q in question["questions"]]
        assert asked == [
            "What kind of presentation is this?", "How should I pitch this?",
        ]
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
        # The format question first, then the clarifying question - and not
        # the handover one the user never implied, nor the pitch-depth
        # fallback, which only fires when nothing else was asked.
        assert asked == [
            "What kind of presentation is this?", "Which breeds should it cover?",
        ]


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
                # INTAKE's slideCount (5) would otherwise disagree with
                # TOOL_RESULTS["plan_presentation_structure"]'s fixed 2 slides
                # and turn this into the deviations path this test is not
                # about - see test_a_slide_count_mismatch_is_flagged_as_a_deviation.
                brief={**json.loads(INTAKE), "slideCount": 0},
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

    @pytest.mark.asyncio
    async def test_a_slide_count_mismatch_is_flagged_as_a_deviation(self, monkeypatch):
        """INTAKE asks for five slides; the mocked plan has two. A prompt line
        asking for a count is a request, not an enforcement - the model can
        and does plan a different one without saying so, and the review
        question is where a real user finds out, before anything is drafted."""
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
        assert "doesn't fully match" in question["question"]
        assert "asked for 5 slides" in question["context"]
        assert "has 2" in question["context"]
        # The outline itself is still there, past the deviation note.
        assert "1. Origins" in question["context"]

    @pytest.mark.asyncio
    async def test_a_model_reported_deviation_is_shown_the_same_way(self, monkeypatch):
        """Not every deviation is a count the loop can check mechanically -
        "cover only 2026 approvals" or "one project per slide" are free text,
        and only the model planning the deck can tell whether it held. Its
        self-report is shown exactly like the mechanical one: leading the
        review message, not buried in the outline."""
        loop, session_id, seen = _loop(
            [],
            state=self._after_consult(
                brief={**json.loads(INTAKE), "slideCount": 2},
                offered_directions=True,
                presentation_structure={
                    **TOOL_RESULTS["plan_presentation_structure"],
                    "deviations": ["Only 2 of the 3 breeds you named turned up material - the third is omitted."],
                },
                reviewed_outline_with_user=False,
            ),
            messages=[{"role": "user", "content": "All 32 recognised"}],
            monkeypatch=monkeypatch,
        )

        events = [e async for e in loop.run(session_id)]

        question = next(e for e in events if e["type"] == "question")
        assert "doesn't fully match" in question["question"]
        assert "Only 2 of the 3 breeds" in question["context"]


class TestRevisionWalksBackwards:
    def test_a_flagged_slide_makes_drafting_unfinished_again(self):
        loop = AgentLoop(db=None, mode="simple")
        loop.questions_declined = True
        ws = loop.working_state
        ws.brief = {"needsResearch": False}
        # A consulted run: the outline review only happens on one.
        ws.consulted_before_planning = True
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
        ws.revision_queue = [1]
        ws.validated_serial = ws.design_serial

        assert pipeline.next_phase(loop).name == "draft"

        # Rewriting it settles the finding: what is outstanding now is a fresh
        # render, not another go at the same slide. (The dispatcher drains the
        # queue as each flagged slide is redrafted.)
        ws.add_slide({"slideIndex": 1, "title": "shorter"})
        ws.revision_queue.remove(1)

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


class TestARevisionIsLabelledAsOne:
    """A first-pass design_slide_content and a fix-up one look identical in
    the feed - same tool name, same-shaped summary - which is what makes
    legitimate, bounded self-correction read as pointless repetition."""

    @pytest.mark.asyncio
    async def test_a_flagged_slide_is_marked_as_a_revision_with_its_reason(self, monkeypatch):
        loop, session_id, _seen = _loop([], monkeypatch=monkeypatch)
        loop.working_state.presentation_structure = TOOL_RESULTS["plan_presentation_structure"]
        loop.working_state.slides_needing_revision = [1]
        loop.working_state.validation_issues = [
            {"slideIndex": 1, "detail": "chart has no data"},
        ]
        loop.working_state.selected_layouts[1] = {"layoutId": "content"}

        events = [
            payload async for kind, payload in loop._call_tool(
                "design_slide_content", {"slide_index": 1},
                context={}, session_id=session_id,
                phase=pipeline.PHASES_BY_NAME["draft"],
            )
            if kind == "event"
        ]

        call = next(e for e in events if e["type"] == "tool_call")
        assert call["isRevision"] is True
        assert call["revisionReason"] == "chart has no data"

        # The slide and the reason travel on the event as fields, not
        # welded onto the summary: the timeline names the slide in the
        # block header and lists the clauses beneath it, so a prefix here
        # is the same two facts a third time inside a row that has to
        # truncate to fit them.
        result = next(e for e in events if e["type"] == "tool_result")
        assert "Fixing Slide 2" not in (result["summary"] or "")

    @pytest.mark.asyncio
    async def test_a_first_pass_carries_no_revision_marker(self, monkeypatch):
        loop, session_id, _seen = _loop([], monkeypatch=monkeypatch)
        loop.working_state.presentation_structure = TOOL_RESULTS["plan_presentation_structure"]
        loop.working_state.selected_layouts[0] = {"layoutId": "content"}

        events = [
            payload async for kind, payload in loop._call_tool(
                "design_slide_content", {"slide_index": 0},
                context={}, session_id=session_id,
                phase=pipeline.PHASES_BY_NAME["draft"],
            )
            if kind == "event"
        ]

        call = next(e for e in events if e["type"] == "tool_call")
        assert "isRevision" not in call

        result = next(e for e in events if e["type"] == "tool_result")
        assert not result["summary"].startswith("Fixing")


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
                # offered_directions=True models a run past that pause, which
                # only makes sense with an answer on record - finalize refuses
                # to render an unsettled look (no chosen direction, no
                # template, no confirmed autonomy).
                "chosen_direction": (
                    {"id": "default"}
                    if phase_name not in ("scout", "consult", "design")
                    else None
                ),
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


class TestTheOutlineReviewAnswerIsActedOn:
    """"Change something - I'll say what" used to change nothing.

    The flag the review phase completes on was set when the question was put,
    not when it was answered, so both options finished the checkpoint
    identically. Nothing cleared the plan, the plan phase completes on that
    plan existing, and the pipeline has no edge back to planning from
    drafting - so the user typed their change and watched the outline they
    had just rejected get built.
    """

    @staticmethod
    def _said(text: str) -> bool:
        from app.agent.loop import user_wants_outline_changed
        return user_wants_outline_changed([{"role": "user", "content": text}])

    def test_the_offered_approval_is_an_approval(self):
        assert self._said("Go ahead as planned") is False

    def test_the_offered_change_is_a_change(self):
        assert self._said(
            "Change something - I'll say what\nDrop the market-size slide."
        ) is True

    def test_a_typed_change_still_counts(self):
        assert self._said("add a slide on pricing before the summary") is True

    def test_and_a_typed_approval_still_counts(self):
        assert self._said("looks good, build it") is False

    def test_an_approval_wins_over_a_loose_change_word(self):
        assert self._said("go ahead as planned, instead of waiting") is False

    def test_the_latest_word_wins(self):
        from app.agent.loop import user_wants_outline_changed

        assert user_wants_outline_changed([
            {"role": "user", "content": "change something - reorder the slides"},
            {"role": "user", "content": "Go ahead as planned"},
        ]) is False

    @pytest.mark.asyncio
    async def test_a_change_reopens_planning(self):
        loop = AgentLoop(db=None, mode="simple")
        ws = loop.working_state
        ws.brief = {"needsResearch": False}
        # A consulted run: the outline review only happens on one.
        ws.consulted_before_planning = True
        ws.offered_directions = True
        ws.presentation_structure = {"totalSlides": 2, "slides": [{}, {}]}
        ws.selected_layouts = {0: "content"}
        ws.reviewed_outline_with_user = True

        assert pipeline.next_phase(loop).name == "draft"

        events = [e async for e in loop._reopen_outline_if_asked(
            [{"role": "user",
              "content": "Change something - I'll say what\nCut the last two."}]
        )]

        assert events, "the user should be told the outline is being replanned"
        assert ws.presentation_structure is None
        assert ws.selected_layouts == {}
        # The new outline gets reviewed too, or the second one ships less
        # examined than the first.
        assert ws.reviewed_outline_with_user is False
        assert pipeline.next_phase(loop).name == "plan"

    @pytest.mark.asyncio
    async def test_an_approval_leaves_the_plan_alone(self):
        loop = AgentLoop(db=None, mode="simple")
        ws = loop.working_state
        ws.brief = {"needsResearch": False}
        # A consulted run: the outline review only happens on one.
        ws.consulted_before_planning = True
        ws.offered_directions = True
        ws.presentation_structure = {"totalSlides": 2, "slides": [{}, {}]}
        ws.reviewed_outline_with_user = True

        events = [e async for e in loop._reopen_outline_if_asked(
            [{"role": "user", "content": "Go ahead as planned"}]
        )]

        assert events == []
        assert ws.presentation_structure is not None
        assert pipeline.next_phase(loop).name == "draft"

    @pytest.mark.asyncio
    async def test_a_change_asked_before_any_plan_exists_is_not_a_reopen(self):
        """The consultation is not the outline review; there is nothing to reopen."""
        loop = AgentLoop(db=None, mode="simple")
        ws = loop.working_state
        ws.brief = {"needsResearch": False}

        events = [e async for e in loop._reopen_outline_if_asked(
            [{"role": "user", "content": "add a slide on pricing"}]
        )]

        assert events == []
        assert ws.reviewed_outline_with_user is False

    @pytest.mark.asyncio
    async def test_a_part_drafted_deck_is_left_alone(self):
        """A change arriving mid-draft is #109, not this checkpoint.

        Nothing walks back to ``review`` once drafting has started, so an
        outline-shaped answer against a part-built deck is a different
        request - and answering it here would throw away finished slides.
        """
        loop = AgentLoop(db=None, mode="simple")
        ws = loop.working_state
        ws.brief = {"needsResearch": False}
        ws.consulted_before_planning = True
        ws.offered_directions = True
        ws.presentation_structure = {"totalSlides": 2, "slides": [{}, {}]}
        ws.reviewed_outline_with_user = True
        ws.add_slide({"slideIndex": 0})

        events = [e async for e in loop._reopen_outline_if_asked(
            [{"role": "user", "content": "add a slide on pricing"}]
        )]

        assert events == []
        assert ws.presentation_structure is not None
        assert ws.accumulated_slides


class TestAStalledRevisionHandsOverWhatItHas:
    """
    A stall after a deck has already rendered once is not the same failure
    as one before anything has. This used to treat them identically -
    "nothing was generated" after every slide was drafted and the deck had
    already rendered, because the redraft loop trying to fix the last few
    flagged slides never converged. Twelve slides of real work is not
    nothing, and the file the first render produced still opens.

    Reproduces the reported shape exactly: draft finishes, render and
    validate find real problems, and every attempt to fix them keeps
    failing - `design_slide_content`/`validate_slide_json` returning
    `success: False` turn after turn, which is what a genuine content or
    schema problem looks like, not a mocked no-op.
    """

    @staticmethod
    def _stalled_after_one_render(**overrides):
        state = {
            "brief": json.loads(INTAKE),
            "scouted": True,
            "consulted_before_planning": True,
            "reviewed_outline_with_user": True,
            "design_system": {"palette": {}, "layouts": []},
            "mode": "deep",
            "detail_level": "detailed",
            "presentation_structure": {
                "totalSlides": 3,
                "slides": [{"slideIndex": i} for i in range(3)],
            },
            "accumulated_slides": [{"slideIndex": i} for i in range(3)],
            "design_serial": 3,
            "rendered_serial": 3,
            "validated_serial": 3,
            "has_called_finalize": True,
            "last_rendered_path": "/tmp/deck.pptx",
            "pending_render_result": {"filePath": "/tmp/deck.pptx",
                                      "previewUrls": []},
            "pending_slide_previews": [
                {"slideIndex": i, "previewUrl": f"/p/{i}.png"} for i in range(3)
            ],
            "previews_published": False,
            "validation_clean": False,
            "validation_issues": [
                {"slideIndex": 1, "type": "overflow",
                 "detail": "text runs off the slide"},
            ],
            "slides_needing_revision": [1],
        }
        state.update(overrides)
        return state

    @pytest.mark.asyncio
    async def test_it_hands_over_the_deck_instead_of_a_bare_error(
            self, monkeypatch):
        # Every attempt to fix slide 1 fails outright - a real content or
        # schema problem, not a no-op - so nothing about working state can
        # change no matter how many turns are spent on it.
        script = [_calls(("design_slide_content", {"slide_index": 1}))
                 for _ in range(20)]
        loop, session_id, seen = _loop(
            script, state=self._stalled_after_one_render(),
            messages=[{"role": "user", "content": "go ahead"}],
            monkeypatch=monkeypatch,
        )

        async def _failing_execute(name, args):
            seen.append(name)
            return ToolResult(success=False, error="that slide will not validate")

        monkeypatch.setattr(loop, "_execute_tool", _failing_execute)

        events = [e async for e in loop.run(session_id)]

        assert not [e for e in events if e["type"] == "error"], (
            "a stall with a render to fall back on must not end on a bare "
            "error - the whole point is that it hands the deck over instead")

        finalized = [e for e in events if e["type"] == "deck_finalized"]
        assert finalized, "the deck from the first render must still be handed over"
        assert finalized[0]["validationClean"] is False

        unresolved = [e for e in events if e["type"] == "validation_unresolved"]
        assert unresolved, "the residual issue must still be stated, not hidden"

    @pytest.mark.asyncio
    async def test_the_thought_names_what_happened(self, monkeypatch):
        script = [_calls(("design_slide_content", {"slide_index": 1}))
                 for _ in range(20)]
        loop, session_id, _ = _loop(
            script, state=self._stalled_after_one_render(),
            messages=[{"role": "user", "content": "go ahead"}],
            monkeypatch=monkeypatch,
        )
        monkeypatch.setattr(
            loop, "_execute_tool",
            lambda name, args: _async(
                ToolResult(success=False, error="will not validate")),
        )

        events = [e async for e in loop.run(session_id)]

        thoughts = " ".join(
            e.get("content", "") for e in events if e["type"] == "thought")
        assert "handing over the deck" in thoughts

    @pytest.mark.asyncio
    async def test_working_state_is_cleared_so_a_resume_starts_clean(
            self, monkeypatch):
        script = [_calls(("design_slide_content", {"slide_index": 1}))
                 for _ in range(20)]
        loop, session_id, _ = _loop(
            script, state=self._stalled_after_one_render(),
            messages=[{"role": "user", "content": "go ahead"}],
            monkeypatch=monkeypatch,
        )
        monkeypatch.setattr(
            loop, "_execute_tool",
            lambda name, args: _async(
                ToolResult(success=False, error="will not validate")),
        )

        _ = [e async for e in loop.run(session_id)]

        assert f"working_state:{session_id}" not in loop.redis_client.store

    @pytest.mark.asyncio
    async def test_a_stall_before_any_render_still_gets_the_bare_error(
            self, monkeypatch):
        """The fallback is specifically for a stall with something to hand
        over. One before the first render ever succeeds still has nothing,
        and the honest answer is still the plain error."""
        script = [_calls(("design_slide_content", {"slide_index": 0}))
                 for _ in range(20)]
        loop, session_id, _ = _loop(
            script,
            state=self._stalled_after_one_render(
                has_called_finalize=False,
                last_rendered_path=None,
                pending_render_result=None,
                pending_slide_previews=[],
                validation_clean=False,
                validation_issues=[],
                slides_needing_revision=[],
                design_serial=0,
                accumulated_slides=[],
                presentation_structure={
                    "totalSlides": 1, "slides": [{"slideIndex": 0}],
                },
            ),
            messages=[{"role": "user", "content": "go ahead"}],
            monkeypatch=monkeypatch,
        )
        monkeypatch.setattr(
            loop, "_execute_tool",
            lambda name, args: _async(
                ToolResult(success=False, error="will not draft")),
        )

        events = [e async for e in loop.run(session_id)]

        errors = [e for e in events if e["type"] == "error"]
        assert errors and errors[0].get("code") == "phase_stalled"


class TestEditDeckWithCodeDuringARevision:
    """The follow-up to #283 ("they can use code whenever it seems
    reasonable") only does something if a script's fix can actually resolve
    a flagged slide - see #288. This drives a real revision pass, scripting
    the model to reach for edit_deck_with_code instead of design_slide_content,
    and checks the whole chain: the tool fires mid-draft, the flag it
    resolves is cleared without a fresh render, the walker returns to
    validate for a real check against the edited file, and the run finishes
    clean rather than stalling or looping.
    """

    @staticmethod
    def _flagged_slide_one(**overrides):
        # `_stalled_after_one_render` does not set `offered_directions`, so
        # without it the walker never reaches `draft` at all - it stalls
        # back at `design` re-offering directions instead, which is a
        # different phase entirely from the one this class is about. Nor
        # does it satisfy `_research_is_thin`, which sits earlier still in
        # the pipeline than `design` - a deck this far along (drafted,
        # rendered, mid-revision) has obviously cleared research too.
        overrides.setdefault("offered_directions", True)
        overrides.setdefault("web_search_calls", 10)
        overrides.setdefault(
            "researched_urls",
            [f"https://example.com/{i}" for i in range(6)],
        )
        # A redraft that falls out of the flagged-slide path re-renders, and
        # finalize_and_render_pptx refuses without a look having been
        # settled one of three ways - fine to have been decided any of
        # those ways for what this class is testing.
        overrides.setdefault("autonomy_confirmed", True)
        return TestAStalledRevisionHandsOverWhatItHas._stalled_after_one_render(
            **overrides
        )

    @pytest.mark.asyncio
    async def test_the_fix_clears_the_flag_and_the_deck_finishes_clean(
            self, monkeypatch):
        script = [_calls(("edit_deck_with_code", {
            "slide_index": 1,
            "code": "from pptx import Presentation\n",
            "intent": "move the overflowing box up",
            "inputs": "none",
        }))]
        loop, session_id, seen = _loop(
            script, state=self._flagged_slide_one(),
            messages=[{"role": "user", "content": "go ahead"}],
            monkeypatch=monkeypatch,
        )
        monkeypatch.setattr(
            AgentLoop, "code_amendment_enabled", property(lambda self: True),
        )

        events = [e async for e in loop.run(session_id)]

        assert "edit_deck_with_code" in seen, (
            "the tool must actually be reachable and called mid-draft, not "
            "just offered"
        )
        assert "finalize_and_render_pptx" not in seen, (
            "a script edit does not bump design_serial, so render must be "
            "skipped rather than overwriting the file the script just fixed"
        )
        assert "validate_rendered_output" in seen, (
            "clearing the flag locally is not enough - the walker has to "
            "come back through validate and check the edited file for real"
        )
        assert not [e for e in events if e["type"] == "error"]
        finalized = [e for e in events if e["type"] == "deck_finalized"]
        assert finalized and finalized[0]["validationClean"] is True

    @pytest.mark.asyncio
    async def test_it_does_not_close_the_unrelated_amend_phase(
            self, monkeypatch):
        """`code_amendment_done` gates a different phase entirely - a fix
        applied while drafting must not mark that backstop as already spent
        for a defect it never saw."""
        script = [_calls(("edit_deck_with_code", {
            "slide_index": 1,
            "code": "from pptx import Presentation\n",
            "intent": "move the overflowing box up",
            "inputs": "none",
        }))]
        loop, session_id, _ = _loop(
            script, state=self._flagged_slide_one(),
            messages=[{"role": "user", "content": "go ahead"}],
            monkeypatch=monkeypatch,
        )
        monkeypatch.setattr(
            AgentLoop, "code_amendment_enabled", property(lambda self: True),
        )

        _ = [e async for e in loop.run(session_id)]

        assert loop.working_state.code_amendment_done is False

    @pytest.mark.asyncio
    async def test_a_successful_edit_counts_as_progress(self, monkeypatch):
        """The other half of #288: before this counter existed, a script fix
        that changed nothing `_progress_marker` was watching looked
        indistinguishable from a turn that did nothing at all."""
        script = [_calls(("edit_deck_with_code", {
            "slide_index": 1,
            "code": "from pptx import Presentation\n",
            "intent": "move the overflowing box up",
            "inputs": "none",
        }))]
        loop, session_id, _ = _loop(
            script, state=self._flagged_slide_one(),
            messages=[{"role": "user", "content": "go ahead"}],
            monkeypatch=monkeypatch,
        )
        monkeypatch.setattr(
            AgentLoop, "code_amendment_enabled", property(lambda self: True),
        )

        _ = [e async for e in loop.run(session_id)]

        assert loop.working_state.code_edits_applied == 1

    @pytest.mark.asyncio
    async def test_the_backstop_edit_is_validated_before_the_deck_is_handed_over(
            self, monkeypatch):
        """The `amend` backstop runs once validation has spent its budget, so
        nothing walked back to `validate` after it: the edit shipped
        unchecked and the closing note listed the findings it had just
        fixed (probe-watermark, #376)."""
        script = [_calls(("edit_deck_with_code", {
            "slide_index": 2,
            "code": "from pptx import Presentation\n",
            "intent": "move the flow up under its copy",
            "inputs": "none",
        }))]
        loop, session_id, seen = _loop(
            script, state=self._flagged_slide_one(
                validation_attempts=WorkingState().MAX_VALIDATION_ATTEMPTS,
                slides_needing_revision=[],
                revision_queue=[],
                validation_issues=[{"slideIndex": 2, "type": "aesthetic",
                                    "detail": "the diagram floats low"}]),
            messages=[{"role": "user", "content": "go ahead"}],
            monkeypatch=monkeypatch,
        )
        monkeypatch.setattr(
            AgentLoop, "code_amendment_enabled", property(lambda self: True),
        )

        events = [e async for e in loop.run(session_id)]

        assert "draft" not in loop._phase_visits
        edited = seen.index("edit_deck_with_code")
        assert "validate_rendered_output" in seen[edited:]
        assert loop.working_state.code_amendment_done is True
        assert loop.working_state.edited_since_validation is False
        finalized = [e for e in events if e["type"] == "deck_finalized"]
        assert finalized and finalized[0]["validationClean"] is True

    @pytest.mark.asyncio
    async def test_a_script_that_misses_gets_re_flagged_not_stuck(
            self, monkeypatch):
        """The edited file still fails validation - a real re-check, not a
        rubber stamp, so the deck goes back through one more revision pass
        rather than being declared clean on the strength of the script
        alone."""
        script = [_calls(("edit_deck_with_code", {
            "slide_index": 1,
            "code": "from pptx import Presentation\n",
            "intent": "move the overflowing box up",
            "inputs": "none",
        }))] + [_calls(("select_layout", {"slide_index": 1}),
                       ("design_slide_content", {"slide_index": 1}))
                for _ in range(10)]
        loop, session_id, seen = _loop(
            script, state=self._flagged_slide_one(),
            messages=[{"role": "user", "content": "go ahead"}],
            monkeypatch=monkeypatch,
        )
        monkeypatch.setattr(
            AgentLoop, "code_amendment_enabled", property(lambda self: True),
        )

        validate_calls = 0

        async def _execute(name, args):
            nonlocal validate_calls
            seen.append(name)
            if name == "validate_rendered_output":
                validate_calls += 1
                # Clean once the redraft has happened, still broken on the
                # check the script's fix alone triggers - the fix genuinely
                # did not work, so a redraft has to be what actually clears
                # it.
                issues = [] if validate_calls > 1 else [
                    {"slideIndex": 1, "type": "overflow",
                     "detail": "still runs off the slide"},
                ]
                return ToolResult(success=True, data={"issues": issues})
            if name == "design_slide_content":
                return ToolResult(success=True, data={
                    "slideIndex": args.get("slide_index"), "title": "t",
                    "contents": [],
                })
            return ToolResult(success=True, data=dict(TOOL_RESULTS.get(name) or {}))

        monkeypatch.setattr(loop, "_execute_tool", _execute)

        events = [e async for e in loop.run(session_id)]

        # It came back through validate a second time on the strength of the
        # edit alone, found the same problem, and fell back to a redraft -
        # not stuck re-running the same script forever.
        assert seen.count("validate_rendered_output") >= 2
        assert "design_slide_content" in seen
        assert not [e for e in events if e["type"] == "error"]


class TestAnAmendmentAnswersOnlyForWhatItChanged:
    """probe-watermark (2026-10-04): asked for a watermark, the run redrafted
    a slide's copy because validation found the empty lower third the deck
    had been handed over with."""

    @pytest.mark.asyncio
    async def test_a_finding_the_deck_already_had_redrafts_nothing(
            self, monkeypatch):
        finding = {"slideIndex": 1, "type": "aesthetic", "fix": "redraft",
                   "detail": "the lower third is empty"}
        state = TestEditDeckWithCodeDuringARevision._flagged_slide_one(
            amending=True, amend_request="Put a DRAFT watermark on every page",
            amend_done=True, validation_attempts=0, validated_serial=-1,
            validation_issues=[], slides_needing_revision=[], revision_queue=[],
            handover_issues=[{"slideIndex": 1, "type": "aesthetic",
                              "detail": "the lower third is empty"}],
        )
        loop, session_id, seen = _loop(
            [], state=state, messages=[{"role": "user", "content": "go"}],
            monkeypatch=monkeypatch,
        )
        monkeypatch.setattr(
            AgentLoop, "code_amendment_enabled", property(lambda self: True),
        )

        async def _execute(name, args):
            seen.append(name)
            if name == "validate_rendered_output":
                return ToolResult(success=True, data={"issues": [dict(finding)]})
            return ToolResult(success=True, data=dict(TOOL_RESULTS.get(name) or {}))

        monkeypatch.setattr(loop, "_execute_tool", _execute)

        events = [e async for e in loop.run(session_id)]

        assert seen.count("validate_rendered_output") == 1
        assert "draft" not in loop._phase_visits
        assert "amend" not in loop._phase_visits
        assert not [e for e in events if e["type"] == "error"]


    @pytest.mark.asyncio
    async def test_what_a_change_broke_is_corrected_by_the_change(
            self, monkeypatch):
        """probe-hyperlink-terms (2026-10-04): a script's blue link on a dark
        cover was answered by re-placing the cover and redrafting its copy.
        It goes back to the change step, once, for a second script."""
        state = TestEditDeckWithCodeDuringARevision._flagged_slide_one(
            amending=True, amend_request="Link every mention of object store",
            amend_done=False, validation_attempts=0, validated_serial=-1,
            validation_issues=[], slides_needing_revision=[], revision_queue=[],
            handover_issues=[],
        )
        edit = ("edit_deck_with_code", {
            "code": "from pptx import Presentation\n",
            "intent": "link the term", "inputs": "none"})
        loop, session_id, seen = _loop(
            [_calls(edit), _calls(edit)], state=state,
            messages=[{"role": "user", "content": "go"}],
            monkeypatch=monkeypatch,
        )
        monkeypatch.setattr(
            AgentLoop, "code_amendment_enabled", property(lambda self: True),
        )
        checks = iter([
            [{"slideIndex": 0, "type": "aesthetic", "fix": "nudge",
              "detail": "the link is blue on the dark ground"}],
            [],
        ])

        async def _execute(name, args):
            seen.append(name)
            if name == "validate_rendered_output":
                return ToolResult(success=True, data={"issues": next(checks)})
            return ToolResult(success=True, data=dict(TOOL_RESULTS.get(name) or {}))

        monkeypatch.setattr(loop, "_execute_tool", _execute)

        events = [e async for e in loop.run(session_id)]

        tools = [s for s in seen if s != "model"]
        assert tools == ["edit_deck_with_code", "validate_rendered_output",
                         "edit_deck_with_code", "validate_rendered_output"]
        assert "draft" not in loop._phase_visits
        finalized = [e for e in events if e["type"] == "deck_finalized"]
        assert finalized and finalized[0]["validationClean"] is True
