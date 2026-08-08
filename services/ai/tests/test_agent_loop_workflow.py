"""
Tests for the agent loop's sequencing enforcement, state-tracking bug fixes,
and resume persistence (issue #20).

No database is used - AgentLoop is constructed with db=None, matching the
pattern in test_agent_mode.py. _load_context and _build_chat_messages (the
only two methods that touch the DB) are monkeypatched where a full _run pass
is needed.
"""

import json
import uuid

import pytest

from app.agent.loop import MODE_SIMPLE, AgentLoop, WorkingState
from app.agent.registry import ToolResult


def _loop(mode: str | None = MODE_SIMPLE) -> AgentLoop:
    return AgentLoop(db=None, mode=mode)


class _FakeRedis:
    """Just enough of the Redis client for the working-state round trip."""

    def __init__(self, initial: dict | None = None):
        self.store = dict(initial or {})

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, key):
        self.store.pop(key, None)


# ── WorkingState.add_slide upserts by slideIndex ──────────────────────────


def test_add_slide_appends_new_indices():
    ws = WorkingState()
    ws.add_slide({"slideIndex": 0, "title": "first"})
    ws.add_slide({"slideIndex": 1, "title": "second"})

    assert [s["slideIndex"] for s in ws.accumulated_slides] == [0, 1]


def test_add_slide_replaces_an_existing_index_instead_of_duplicating():
    """A redesigned slide must overwrite, not accumulate duplicates that would
    both get rendered."""
    ws = WorkingState()
    ws.add_slide({"slideIndex": 0, "title": "first draft"})
    ws.add_slide({"slideIndex": 1, "title": "second"})
    ws.add_slide({"slideIndex": 0, "title": "revised"})

    assert len(ws.accumulated_slides) == 2
    assert ws.accumulated_slides[0]["title"] == "revised"


# ── Precondition gating ────────────────────────────────────────────────────


def test_design_slide_content_blocked_without_a_plan():
    loop = _loop()

    error = loop._check_precondition("design_slide_content", {})

    assert error is not None
    assert "plan_presentation_structure" in error


def test_design_slide_content_allowed_once_structure_exists():
    loop = _loop()
    loop.working_state.presentation_structure = {"totalSlides": 2}

    assert loop._check_precondition("design_slide_content", {}) is None
    assert loop._check_precondition("select_layout", {}) is None
    assert loop._check_precondition("generate_image_prompt", {}) is None


def test_replanning_blocked_once_slides_have_been_designed():
    loop = _loop()
    loop.working_state.presentation_structure = {"totalSlides": 2}
    loop.working_state.add_slide({"slideIndex": 0})

    error = loop._check_precondition("plan_presentation_structure", {})

    assert error is not None
    assert "ask_user_question" in error


def test_replanning_allowed_before_any_slide_is_designed():
    loop = _loop()
    loop.working_state.presentation_structure = {"totalSlides": 2}

    assert loop._check_precondition("plan_presentation_structure", {}) is None


def test_finalize_blocked_until_every_planned_slide_is_designed():
    loop = _loop()
    loop.working_state.presentation_structure = {"totalSlides": 3}
    loop.working_state.add_slide({"slideIndex": 0})

    error = loop._check_precondition("finalize_and_render_pptx", {})

    assert error is not None
    assert "1 and 2" in error or "[1, 2]" in error


def test_finalize_allowed_once_all_planned_slides_are_designed():
    loop = _loop()
    loop.working_state.presentation_structure = {"totalSlides": 2}
    loop.working_state.add_slide({"slideIndex": 0})
    loop.working_state.add_slide({"slideIndex": 1})

    assert loop._check_precondition("finalize_and_render_pptx", {}) is None


def test_finalize_blocked_without_a_plan_at_all():
    loop = _loop()

    error = loop._check_precondition("finalize_and_render_pptx", {})

    assert error is not None
    assert "plan_presentation_structure" in error


def test_design_slide_content_blocked_without_a_layout_for_that_slide():
    loop = _loop()
    loop.working_state.presentation_structure = {"totalSlides": 2}
    loop.working_state.selected_layouts[0] = {"layoutId": "content"}

    # Slide 0 has a layout, slide 1 does not.
    assert loop._check_precondition("design_slide_content", {"slide_index": 0}) is None
    error = loop._check_precondition("design_slide_content", {"slide_index": 1})
    assert error is not None
    assert "select_layout" in error
    assert "slide_index=1" in error


def test_design_slide_content_with_no_slide_index_is_not_blocked_by_layout_check():
    """A malformed call missing slide_index shouldn't be blocked by the
    layout check specifically - the tool itself will reject it."""
    loop = _loop()
    loop.working_state.presentation_structure = {"totalSlides": 2}

    assert loop._check_precondition("design_slide_content", {}) is None


def test_completed_work_summary_handles_a_slide_with_empty_contents():
    """Regression: accumulated_slides was dead code until this fix, so a slide
    with an empty ``contents`` list (a real possibility - e.g. a render
    failure before any content item was added) never hit this path. Once
    reachable, list[0] on an empty list raised IndexError."""
    ws = WorkingState()
    ws.record_completed_tool("design_slide_content", {}, {})
    ws.add_slide({"slideIndex": 0, "contents": []})

    summary = ws.get_completed_work_summary()

    assert "Slide 1" in summary


def test_tools_with_no_precondition_are_never_blocked():
    loop = _loop()

    for name in ("analyze_document", "extract_key_data", "web_search", "ask_user_question", "validate_slide_json"):
        assert loop._check_precondition(name, {}) is None


# ── System prompt: ask-by-default with an explicit opt-out ────────────────


def test_prompt_defaults_to_asking_when_unsure():
    prompt = _loop(MODE_SIMPLE)._build_system_prompt({})

    assert "DEFAULT TO ASKING, NOT GUESSING" in prompt


def test_prompt_states_the_explicit_opt_out():
    prompt = _loop(MODE_SIMPLE)._build_system_prompt({})

    assert "don't ask me anything" in prompt
    assert "EXCEPTION" in prompt


# ── design_slide_content / plan_presentation_structure result handling ────
# Both tools return their payload directly as tool_result.data (not nested
# under "slide"/"structure"), so working_state population is exercised via a
# minimal, fully-stubbed pass through _run rather than re-testing the tools.


@pytest.mark.asyncio
async def test_run_tracks_structure_and_slides_and_auto_assembles_finalize_payload(monkeypatch):
    session_id = uuid.uuid4()
    loop = _loop()
    loop.redis_client = _FakeRedis()

    async def _no_context(*args, **kwargs):
        return {}

    async def _no_messages(*args, **kwargs):
        return [{"role": "user", "content": "Make a 2-slide deck about cats"}]

    monkeypatch.setattr(loop, "_load_context", _no_context)
    monkeypatch.setattr(loop, "_build_chat_messages", _no_messages)

    structure = {"title": "Cats", "totalSlides": 2, "slides": [{"slideIndex": 0}, {"slideIndex": 1}]}
    layout0 = {"layoutId": "1", "layoutName": "Content"}
    layout1 = {"layoutId": "1", "layoutName": "Content"}
    slide0 = {"slideIndex": 0, "layoutId": "1", "layoutName": "Content", "contents": []}
    slide1 = {"slideIndex": 1, "layoutId": "1", "layoutName": "Content", "contents": []}

    # Scripted tool calls: plan -> (select layout, design) per slide -> finalize -> stop.
    script = [
        [{"id": "1", "function": {"name": "plan_presentation_structure", "arguments": "{}"}}],
        [{"id": "2", "function": {"name": "select_layout", "arguments": json.dumps({"content_type": "content", "slide_index": 0})}}],
        [{"id": "3", "function": {"name": "design_slide_content", "arguments": json.dumps({"slide_index": 0})}}],
        [{"id": "4", "function": {"name": "select_layout", "arguments": json.dumps({"content_type": "content", "slide_index": 1})}}],
        [{"id": "5", "function": {"name": "design_slide_content", "arguments": json.dumps({"slide_index": 1})}}],
        [{"id": "6", "function": {"name": "finalize_and_render_pptx", "arguments": json.dumps({"slides_json": "[]", "session_id": str(session_id)})}}],
        [],
    ]
    calls = {"n": 0}

    async def _chat(*args, **kwargs):
        idx = calls["n"]
        calls["n"] += 1
        tool_calls = script[idx] if idx < len(script) else []
        content = "Done." if not tool_calls else ""
        return {"message": {"content": content}, "tool_calls": tool_calls}

    monkeypatch.setattr(loop, "_chat_with_retry", _chat)

    captured_finalize_args: dict = {}

    async def _execute_tool(name, args):
        if name == "plan_presentation_structure":
            return ToolResult(success=True, data=structure)
        if name == "select_layout":
            layout = layout0 if args.get("slide_index") == 0 else layout1
            return ToolResult(success=True, data=layout)
        if name == "design_slide_content":
            slide = slide0 if args.get("slide_index") == 0 else slide1
            return ToolResult(success=True, data=slide)
        if name == "finalize_and_render_pptx":
            captured_finalize_args.update(args)
            return ToolResult(success=True, data={"filePath": "/x.pptx", "fileName": "x.pptx", "previewUrls": []})
        raise AssertionError(f"unexpected tool {name}")

    monkeypatch.setattr(loop, "_execute_tool", _execute_tool)

    events = [ev async for ev in loop.run(session_id)]

    assert loop.working_state.presentation_structure == structure
    assert [s["slideIndex"] for s in loop.working_state.accumulated_slides] == [0, 1]
    assert loop.working_state.selected_layouts == {0: layout0, 1: layout1}
    assert loop.working_state.has_called_finalize is True

    # finalize_and_render_pptx must have been called with the accumulated
    # slides, not the (empty, LLM-supplied) "[]" placeholder.
    assert json.loads(captured_finalize_args["slides_json"]) == [slide0, slide1]

    tool_results = [e for e in events if e["type"] == "tool_result"]
    assert all(e["success"] for e in tool_results)


@pytest.mark.asyncio
async def test_design_system_blobs_are_server_supplied_not_trusted_from_the_llm(monkeypatch):
    """Regression: select_layout/design_slide_content/plan_presentation_structure
    ask for large design-system JSON blobs as string parameters, which the LLM
    reliably mis-escapes once they're non-trivial (observed: "Expecting
    property name enclosed in double quotes", from double-escaped quotes).
    The loop must override whatever the LLM sends with its own json.dumps of
    the tracked design system / selected layout, so a malformed value from
    the LLM never reaches the tool."""
    session_id = uuid.uuid4()
    loop = _loop()
    # Seeded via Redis (not assigned directly to loop.working_state) so that
    # _run's state_loaded check is True and skips working_state.reset(),
    # which would otherwise wipe presentation_structure before the script runs.
    seeded_state = json.dumps({"completed_tools": {}, "presentation_structure": {"totalSlides": 1}})
    loop.redis_client = _FakeRedis({f"working_state:{session_id}": seeded_state})

    design_system = {"layouts": [{"layoutId": "content", "layoutName": "Content"}], "palette": {}}

    async def _with_design_system(*args, **kwargs):
        return {"design_system": design_system}

    async def _no_messages(*args, **kwargs):
        return []

    monkeypatch.setattr(loop, "_load_context", _with_design_system)
    monkeypatch.setattr(loop, "_build_chat_messages", _no_messages)

    garbage = '[{\\\\"layoutId\\\\":\\\\"content\\\\"'  # the exact double-escaped shape reported in production
    script = [
        [{"id": "1", "function": {"name": "select_layout", "arguments": json.dumps({
            "content_type": "content", "slide_index": 0, "available_layouts": garbage,
        })}}],
        [],
    ]
    calls = {"n": 0}

    async def _chat(*args, **kwargs):
        idx = calls["n"]
        calls["n"] += 1
        tool_calls = script[idx] if idx < len(script) else []
        return {"message": {"content": "" if tool_calls else "Done."}, "tool_calls": tool_calls}

    monkeypatch.setattr(loop, "_chat_with_retry", _chat)
    loop.MAX_ITERATIONS = 2

    captured_args: dict = {}

    async def _execute_tool(name, args):
        captured_args.update(args)
        return ToolResult(success=True, data={"layoutId": "content"})

    monkeypatch.setattr(loop, "_execute_tool", _execute_tool)

    async for _ in loop.run(session_id):
        pass

    # The loop's own json.dumps of the tracked design system, not the
    # LLM-supplied garbage string.
    assert json.loads(captured_args["available_layouts"]) == design_system["layouts"]


@pytest.mark.asyncio
async def test_design_slide_content_receives_the_select_layout_result_automatically(monkeypatch):
    """The loop must not require the LLM to copy the select_layout result
    back verbatim into design_slide_content - same escaping risk as
    available_layouts, for the same reason."""
    session_id = uuid.uuid4()
    loop = _loop()
    the_layout = {"layoutId": "content", "layoutName": "Content", "placeholders": []}
    # Seeded via Redis so _run's state_loaded check is True and skips
    # working_state.reset(), which would otherwise wipe these before the
    # script runs.
    seeded_state = json.dumps({
        "completed_tools": {},
        "presentation_structure": {"totalSlides": 1},
        "selected_layouts": {"0": the_layout},
    })
    loop.redis_client = _FakeRedis({f"working_state:{session_id}": seeded_state})

    monkeypatch.setattr(loop, "_load_context", lambda *a, **k: _async({}))
    monkeypatch.setattr(loop, "_build_chat_messages", lambda *a, **k: _async([]))

    script = [
        [{"id": "1", "function": {"name": "design_slide_content", "arguments": json.dumps({
            "slide_outline": "{}", "slide_index": 0, "layout_info": "not even json",
        })}}],
        [],
    ]
    calls = {"n": 0}

    async def _chat(*args, **kwargs):
        idx = calls["n"]
        calls["n"] += 1
        tool_calls = script[idx] if idx < len(script) else []
        return {"message": {"content": "" if tool_calls else "Done."}, "tool_calls": tool_calls}

    monkeypatch.setattr(loop, "_chat_with_retry", _chat)
    loop.MAX_ITERATIONS = 2

    captured_args: dict = {}

    async def _execute_tool(name, args):
        captured_args.update(args)
        return ToolResult(success=True, data={"slideIndex": 0, "contents": []})

    monkeypatch.setattr(loop, "_execute_tool", _execute_tool)

    async for _ in loop.run(session_id):
        pass

    assert json.loads(captured_args["layout_info"]) == the_layout


@pytest.mark.asyncio
async def test_finalize_called_too_early_is_rejected_without_invoking_the_tool(monkeypatch):
    session_id = uuid.uuid4()
    loop = _loop()
    loop.redis_client = _FakeRedis()
    loop.MAX_ITERATIONS = 2  # only the one blocked call matters here

    monkeypatch.setattr(loop, "_load_context", lambda *a, **k: _async({}))
    monkeypatch.setattr(loop, "_build_chat_messages", lambda *a, **k: _async([]))

    script = [
        [{"id": "1", "function": {"name": "finalize_and_render_pptx", "arguments": "{}"}}],
        [],
    ]
    calls = {"n": 0}

    async def _chat(*args, **kwargs):
        idx = calls["n"]
        calls["n"] += 1
        tool_calls = script[idx] if idx < len(script) else []
        return {"message": {"content": "" if tool_calls else "Done."}, "tool_calls": tool_calls}

    monkeypatch.setattr(loop, "_chat_with_retry", _chat)

    async def _execute_tool(name, args):
        raise AssertionError("finalize_and_render_pptx must not run without a plan")

    monkeypatch.setattr(loop, "_execute_tool", _execute_tool)

    events = [ev async for ev in loop.run(session_id)]

    tool_results = [e for e in events if e["type"] == "tool_result"]
    assert len(tool_results) == 1
    assert tool_results[0]["success"] is False
    assert "plan_presentation_structure" in tool_results[0]["result"]
    assert loop.working_state.has_called_finalize is False


def _async(value):
    async def _inner(*args, **kwargs):
        return value
    return _inner()


# ── Issue #20: design system + research survive a pause/resume cycle ──────


@pytest.mark.asyncio
async def test_save_working_state_persists_design_system_and_research():
    session_id = uuid.uuid4()
    loop = _loop()
    loop.redis_client = _FakeRedis()
    loop.context = {
        "design_system": {"palette": {"colors": ["#fff"]}},
        "research": [{"title": "A", "url": "https://example.com", "snippet": "s"}],
    }

    await loop._save_working_state(session_id)

    saved = json.loads(loop.redis_client.store[f"working_state:{session_id}"])
    assert saved["design_system"] == {"palette": {"colors": ["#fff"]}}
    assert saved["research"] == [{"title": "A", "url": "https://example.com", "snippet": "s"}]


@pytest.mark.asyncio
async def test_load_working_state_stashes_design_system_and_research_for_run_to_merge():
    session_id = uuid.uuid4()
    state = json.dumps({
        "completed_tools": {},
        "design_system": {"palette": {"colors": ["#000"]}},
        "research": [{"title": "B", "url": "https://example.org", "snippet": "s2"}],
    })
    loop = _loop(None)
    loop.redis_client = _FakeRedis({f"working_state:{session_id}": state})

    assert await loop._load_working_state(session_id) is True

    assert loop._resumed_design_system == {"palette": {"colors": ["#000"]}}
    assert loop._resumed_research == [{"title": "B", "url": "https://example.org", "snippet": "s2"}]


@pytest.mark.asyncio
async def test_run_merges_resumed_design_system_and_research_into_context(monkeypatch):
    """End-to-end: a run resumed after ask_user_question must not re-extract
    the design system or lose prior web research (issue #20)."""
    session_id = uuid.uuid4()
    state = json.dumps({
        "completed_tools": {},
        "design_system": {"palette": {"colors": ["#000"]}},
        "research": [{"title": "B", "url": "https://example.org", "snippet": "s2"}],
    })
    loop = _loop(None)
    loop.redis_client = _FakeRedis({f"working_state:{session_id}": state})

    monkeypatch.setattr(loop, "_load_context", lambda *a, **k: _async({}))
    monkeypatch.setattr(loop, "_build_chat_messages", lambda *a, **k: _async([]))

    async def _chat(*args, **kwargs):
        return {"message": {"content": ""}, "tool_calls": []}

    # First call: no finalize yet, forced to continue; stop iterating manually
    # by raising after we can inspect merged context.
    monkeypatch.setattr(loop, "_chat_with_retry", _chat)
    loop.MAX_ITERATIONS = 1

    async for _ in loop.run(session_id):
        pass

    assert loop.context["design_system"] == {"palette": {"colors": ["#000"]}}
    assert loop.context["research"] == [{"title": "B", "url": "https://example.org", "snippet": "s2"}]
