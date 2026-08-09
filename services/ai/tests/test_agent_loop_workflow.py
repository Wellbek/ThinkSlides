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
    assert loop._check_precondition("select_source_image", {}) is None


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
async def test_ask_user_question_stops_the_run_instead_of_calling_the_llm_again(monkeypatch):
    """Regression: a bare `break` inside the tool-calls loop only exited that
    inner loop - the outer iteration loop then continued and called the LLM
    again immediately, with the just-appended assistant message's tool_calls
    still unanswered (ask_user_question's own tool_call_id never got a
    paired tool-role response). Strict providers (OpenAI) reject that
    outright: "An assistant message with 'tool_calls' must be followed by
    tool messages...". The run must stop completely after pausing, not
    continue to a second (malformed) LLM call."""
    session_id = uuid.uuid4()
    loop = _loop()
    loop.redis_client = _FakeRedis()

    monkeypatch.setattr(loop, "_load_context", lambda *a, **k: _async({}))
    monkeypatch.setattr(loop, "_build_chat_messages", lambda *a, **k: _async([]))

    calls = {"n": 0}

    async def _chat(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] > 1:
            raise AssertionError(
                "the LLM must not be called again after ask_user_question pauses - "
                "the run should have stopped entirely"
            )
        return {
            "message": {"content": ""},
            "tool_calls": [{"id": "call_1", "function": {
                "name": "ask_user_question",
                "arguments": json.dumps({"question": "Focus on what?", "options": ["A", "B"]}),
            }}],
        }

    monkeypatch.setattr(loop, "_chat_with_retry", _chat)

    async def _execute_tool(name, args):
        assert name == "ask_user_question"
        return ToolResult(success=True, data={"question": args["question"], "options": ["A", "B"], "context": ""})

    monkeypatch.setattr(loop, "_execute_tool", _execute_tool)

    events = [ev async for ev in loop.run(session_id)]

    assert calls["n"] == 1
    event_types = [e["type"] for e in events]
    assert "question" in event_types
    assert event_types[-1] == "done"
    assert events[-1]["paused"] is True
    assert "error" not in event_types
    assert loop.working_state.is_paused_for_user is True


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


# ── WorkingState.resolve_and_add_slide: "selected" image sentinel ─────────
# design_slide_content is told about a pre-vetted image via
# selected_image_context and asked to reference it as a lightweight
# {"source": "selected"} sentinel rather than inventing/copying raw image
# data - this is the server-side step that turns that sentinel into real
# data, or drops it when nothing was actually selected.


def test_resolve_and_add_slide_fills_in_the_selected_image():
    ws = WorkingState()
    ws.selected_images[0] = {"source": "base64", "data": "data:image/png;base64,AAAA", "altText": "chart"}
    slide = {
        "slideIndex": 0,
        "contents": [
            {"type": "text", "runs": [{"text": "Title"}]},
            {"type": "image", "image": {"source": "selected"}},
        ],
    }

    ws.resolve_and_add_slide(slide)

    stored = ws.accumulated_slides[0]
    image_items = [c for c in stored["contents"] if c["type"] == "image"]
    assert len(image_items) == 1
    assert image_items[0]["image"]["source"] == "base64"
    assert image_items[0]["image"]["data"] == "data:image/png;base64,AAAA"


def test_resolve_and_add_slide_drops_the_sentinel_when_nothing_was_selected():
    ws = WorkingState()  # no entry in selected_images for slide 0
    slide = {
        "slideIndex": 0,
        "contents": [
            {"type": "text", "runs": [{"text": "Title"}]},
            {"type": "image", "image": {"source": "selected"}},
        ],
    }

    ws.resolve_and_add_slide(slide)

    stored = ws.accumulated_slides[0]
    assert [c["type"] for c in stored["contents"]] == ["text"]


def test_resolve_and_add_slide_preserves_placeholder_idx_from_the_sentinel():
    ws = WorkingState()
    ws.selected_images[0] = {"source": "url", "data": "https://example.com/photo.jpg"}
    slide = {
        "slideIndex": 0,
        "contents": [{"type": "image", "placeholderIdx": 2, "image": {"source": "selected"}}],
    }

    ws.resolve_and_add_slide(slide)

    resolved = ws.accumulated_slides[0]["contents"][0]
    assert resolved["image"]["placeholderIdx"] == 2
    assert resolved["image"]["data"] == "https://example.com/photo.jpg"


def test_resolve_and_add_slide_leaves_non_selected_image_content_untouched():
    """An image with an already-real source (e.g. from an older run/schema
    path) must not be treated as a sentinel and must not be dropped."""
    ws = WorkingState()
    slide = {
        "slideIndex": 0,
        "contents": [{"type": "image", "image": {"source": "base64", "data": "data:image/png;base64,ZZZZ"}}],
    }

    ws.resolve_and_add_slide(slide)

    assert ws.accumulated_slides[0]["contents"][0]["image"]["data"] == "data:image/png;base64,ZZZZ"


def test_resolve_and_add_slide_handles_missing_contents_gracefully():
    ws = WorkingState()
    ws.resolve_and_add_slide({"slideIndex": 0})
    assert ws.accumulated_slides == [{"slideIndex": 0}]


# ── Full loop: select_source_image -> design_slide_content resolution ─────


@pytest.mark.asyncio
async def test_selected_source_image_flows_through_the_full_loop_into_the_slide(monkeypatch):
    """Exercises the catalog auto-injection into select_source_image, the
    loop resolving its chosen id back to real data, and design_slide_content's
    "selected" sentinel being filled in - all together, through _run."""
    session_id = uuid.uuid4()
    loop = _loop()
    loop.redis_client = _FakeRedis()

    catalog_entry = {
        "id": "doc-report.pdf-p1-0",
        "sourceType": "document",
        "data": "data:image/png;base64,AAAA",
        "context": "Quarterly revenue chart",
    }

    async def _with_catalog(*args, **kwargs):
        return {"imageCatalog": [dict(catalog_entry)]}

    async def _no_messages(*args, **kwargs):
        return [{"role": "user", "content": "Make a slide about revenue"}]

    monkeypatch.setattr(loop, "_load_context", _with_catalog)
    monkeypatch.setattr(loop, "_build_chat_messages", _no_messages)

    structure = {"title": "T", "totalSlides": 1, "slides": [{"slideIndex": 0}]}
    layout0 = {"layoutId": "1", "layoutName": "Content"}
    slide_with_sentinel = {
        "slideIndex": 0, "layoutId": "1", "layoutName": "Content",
        "contents": [{"type": "image", "image": {"source": "selected"}}],
    }

    script = [
        [{"id": "1", "function": {"name": "plan_presentation_structure", "arguments": "{}"}}],
        [{"id": "2", "function": {"name": "select_layout", "arguments": json.dumps({"content_type": "content", "slide_index": 0})}}],
        [{"id": "3", "function": {"name": "select_source_image", "arguments": json.dumps({"slide_index": 0, "slide_topic": "revenue"})}}],
        [{"id": "4", "function": {"name": "design_slide_content", "arguments": json.dumps({"slide_index": 0})}}],
        [{"id": "5", "function": {"name": "finalize_and_render_pptx", "arguments": json.dumps({"slides_json": "[]", "session_id": str(session_id)})}}],
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
        if name == "plan_presentation_structure":
            return ToolResult(success=True, data=structure)
        if name == "select_layout":
            return ToolResult(success=True, data=layout0)
        if name == "select_source_image":
            assert args["slide_index"] == 0
            # The loop must have supplied the catalog itself (id + context only).
            supplied = json.loads(args["catalog_json"])
            assert supplied == [{"id": "doc-report.pdf-p1-0", "context": "Quarterly revenue chart"}]
            return ToolResult(success=True, data={
                "slideIndex": 0, "selected": True, "imageId": "doc-report.pdf-p1-0", "reason": "match",
            })
        if name == "design_slide_content":
            # The resolved image must have been auto-supplied for this slide.
            supplied_image = json.loads(args["selected_image_context"])
            assert supplied_image["data"] == "data:image/png;base64,AAAA"
            return ToolResult(success=True, data=slide_with_sentinel)
        if name == "finalize_and_render_pptx":
            return ToolResult(success=True, data={"filePath": "/x.pptx", "fileName": "x.pptx", "previewUrls": []})
        raise AssertionError(f"unexpected tool {name}")

    monkeypatch.setattr(loop, "_execute_tool", _execute_tool)

    async for _ in loop.run(session_id):
        pass

    assert loop.working_state.selected_images[0]["data"] == "data:image/png;base64,AAAA"
    rendered = loop.working_state.accumulated_slides[0]
    image_items = [c for c in rendered["contents"] if c.get("type") == "image"]
    assert len(image_items) == 1
    assert image_items[0]["image"]["source"] == "base64"
    assert image_items[0]["image"]["data"] == "data:image/png;base64,AAAA"


@pytest.mark.asyncio
async def test_no_confident_selection_means_no_image_on_the_slide(monkeypatch):
    """select_source_image declining to select anything must leave the
    slide's "selected" sentinel dropped, not crash or ship a broken ref."""
    session_id = uuid.uuid4()
    loop = _loop()
    loop.redis_client = _FakeRedis()

    async def _with_catalog(*args, **kwargs):
        return {"imageCatalog": [{"id": "x", "sourceType": "document", "data": "d", "context": "c"}]}

    async def _no_messages(*args, **kwargs):
        return [{"role": "user", "content": "Make a slide"}]

    monkeypatch.setattr(loop, "_load_context", _with_catalog)
    monkeypatch.setattr(loop, "_build_chat_messages", _no_messages)

    structure = {"title": "T", "totalSlides": 1, "slides": [{"slideIndex": 0}]}
    layout0 = {"layoutId": "1", "layoutName": "Content"}
    slide_with_sentinel = {
        "slideIndex": 0, "layoutId": "1", "layoutName": "Content",
        "contents": [
            {"type": "text", "runs": [{"text": "Title"}]},
            {"type": "image", "image": {"source": "selected"}},
        ],
    }

    script = [
        [{"id": "1", "function": {"name": "plan_presentation_structure", "arguments": "{}"}}],
        [{"id": "2", "function": {"name": "select_layout", "arguments": json.dumps({"content_type": "content", "slide_index": 0})}}],
        [{"id": "3", "function": {"name": "select_source_image", "arguments": json.dumps({"slide_index": 0, "slide_topic": "x"})}}],
        [{"id": "4", "function": {"name": "design_slide_content", "arguments": json.dumps({"slide_index": 0})}}],
        [{"id": "5", "function": {"name": "finalize_and_render_pptx", "arguments": json.dumps({"slides_json": "[]", "session_id": str(session_id)})}}],
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
        if name == "plan_presentation_structure":
            return ToolResult(success=True, data=structure)
        if name == "select_layout":
            return ToolResult(success=True, data=layout0)
        if name == "select_source_image":
            return ToolResult(success=True, data={"slideIndex": 0, "selected": False, "imageId": None, "reason": "no match"})
        if name == "design_slide_content":
            return ToolResult(success=True, data=slide_with_sentinel)
        if name == "finalize_and_render_pptx":
            return ToolResult(success=True, data={"filePath": "/x.pptx", "fileName": "x.pptx", "previewUrls": []})
        raise AssertionError(f"unexpected tool {name}")

    monkeypatch.setattr(loop, "_execute_tool", _execute_tool)

    async for _ in loop.run(session_id):
        pass

    assert 0 not in loop.working_state.selected_images
    rendered = loop.working_state.accumulated_slides[0]
    assert [c["type"] for c in rendered["contents"]] == ["text"]


# ── validate_rendered_output: precondition, retry cap, mandatory-exit gate ─


def test_validate_rendered_output_blocked_before_finalize():
    loop = _loop()
    loop.working_state.presentation_structure = {"totalSlides": 1}

    error = loop._check_precondition("validate_rendered_output", {})

    assert error is not None
    assert "finalize_and_render_pptx" in error


def test_validate_rendered_output_allowed_once_finalized():
    loop = _loop()
    loop.working_state.has_called_finalize = True

    assert loop._check_precondition("validate_rendered_output", {}) is None


def test_validate_rendered_output_blocked_once_the_retry_cap_is_reached():
    loop = _loop()
    loop.working_state.has_called_finalize = True
    loop.working_state.validation_attempts = loop.working_state.MAX_VALIDATION_ATTEMPTS

    error = loop._check_precondition("validate_rendered_output", {})

    assert error is not None
    assert "retry limit" in error.lower()


@pytest.mark.asyncio
async def test_exit_is_blocked_after_finalize_alone_without_validating(monkeypatch):
    """The loop must not let the LLM stop right after finalize - it must
    also call validate_rendered_output at least once."""
    session_id = uuid.uuid4()
    loop = _loop()
    loop.redis_client = _FakeRedis()

    async def _no_context(*a, **k):
        return {}

    async def _no_messages(*a, **k):
        return []

    monkeypatch.setattr(loop, "_load_context", _no_context)
    monkeypatch.setattr(loop, "_build_chat_messages", _no_messages)

    structure = {"title": "T", "totalSlides": 1, "slides": [{"slideIndex": 0}]}
    layout0 = {"layoutId": "1", "layoutName": "Content"}
    slide0 = {"slideIndex": 0, "layoutId": "1", "layoutName": "Content", "contents": []}

    # Script: after finalize succeeds, the LLM tries to stop (empty tool_calls)
    # - the loop must force it to continue instead of accepting that as done.
    script = [
        [{"id": "1", "function": {"name": "plan_presentation_structure", "arguments": "{}"}}],
        [{"id": "2", "function": {"name": "select_layout", "arguments": json.dumps({"content_type": "content", "slide_index": 0})}}],
        [{"id": "3", "function": {"name": "design_slide_content", "arguments": json.dumps({"slide_index": 0})}}],
        [{"id": "4", "function": {"name": "finalize_and_render_pptx", "arguments": json.dumps({"session_id": str(session_id)})}}],
        [],  # LLM tries to stop here without validating
        [{"id": "5", "function": {"name": "validate_rendered_output", "arguments": json.dumps({"pptx_path": "/x.pptx", "session_id": str(session_id)})}}],
        [],  # now allowed to stop
    ]
    calls = {"n": 0}

    async def _chat(*a, **k):
        idx = calls["n"]
        calls["n"] += 1
        tool_calls = script[idx] if idx < len(script) else []
        return {"message": {"content": "" if tool_calls else "Done."}, "tool_calls": tool_calls}

    monkeypatch.setattr(loop, "_chat_with_retry", _chat)
    loop.MAX_ITERATIONS = 10

    async def _execute_tool(name, args):
        if name == "plan_presentation_structure":
            return ToolResult(success=True, data=structure)
        if name == "select_layout":
            return ToolResult(success=True, data=layout0)
        if name == "design_slide_content":
            return ToolResult(success=True, data=slide0)
        if name == "finalize_and_render_pptx":
            return ToolResult(success=True, data={"filePath": "/x.pptx", "fileName": "x.pptx", "previewUrls": []})
        if name == "validate_rendered_output":
            return ToolResult(success=True, data={"sessionId": str(session_id), "valid": True, "issueCount": 0, "issues": []})
        raise AssertionError(f"unexpected tool {name}")

    monkeypatch.setattr(loop, "_execute_tool", _execute_tool)

    events = [ev async for ev in loop.run(session_id)]

    assert loop.working_state.has_called_finalize is True
    assert loop.working_state.has_called_validate is True
    tool_names_called = [e["toolName"] for e in events if e["type"] == "tool_call"]
    assert tool_names_called.count("validate_rendered_output") == 1


@pytest.mark.asyncio
async def test_validation_narration_is_server_authored(monkeypatch):
    """The chat must show an explicit, server-written status for the
    validation step - not left to the LLM to describe or omit."""
    session_id = uuid.uuid4()
    loop = _loop()
    # Seeded via Redis (not assigned directly to loop.working_state) so
    # _run's state_loaded check is True and skips working_state.reset(),
    # which would otherwise wipe has_called_finalize before the script runs.
    seeded_state = json.dumps({
        "completed_tools": {}, "has_called_finalize": True, "last_rendered_path": "/x.pptx",
    })
    loop.redis_client = _FakeRedis({f"working_state:{session_id}": seeded_state})

    async def _no_context(*a, **k):
        return {}

    async def _no_messages(*a, **k):
        return []

    monkeypatch.setattr(loop, "_load_context", _no_context)
    monkeypatch.setattr(loop, "_build_chat_messages", _no_messages)

    script = [
        [{"id": "1", "function": {"name": "validate_rendered_output", "arguments": "{}"}}],
        [],
    ]
    calls = {"n": 0}

    async def _chat(*a, **k):
        idx = calls["n"]
        calls["n"] += 1
        tool_calls = script[idx] if idx < len(script) else []
        return {"message": {"content": "" if tool_calls else "Done."}, "tool_calls": tool_calls}

    monkeypatch.setattr(loop, "_chat_with_retry", _chat)
    loop.MAX_ITERATIONS = 3

    async def _execute_tool(name, args):
        return ToolResult(success=True, data={"sessionId": str(session_id), "valid": True, "issueCount": 0, "issues": []})

    monkeypatch.setattr(loop, "_execute_tool", _execute_tool)

    events = [ev async for ev in loop.run(session_id)]

    thoughts = [e["content"] for e in events if e["type"] == "thought"]
    assert any("Validating the rendered output with LibreOffice" in t for t in thoughts)


@pytest.mark.asyncio
async def test_working_state_survives_between_finalize_and_validate(monkeypatch):
    """Regression guard: working state must not be cleared at finalize
    anymore - only once validation has actually run (or hit its cap) -
    otherwise a crash/resume between the two steps would lose has_called_finalize
    and last_rendered_path."""
    session_id = uuid.uuid4()
    loop = _loop()
    loop.redis_client = _FakeRedis()

    async def _no_context(*a, **k):
        return {}

    async def _no_messages(*a, **k):
        return []

    monkeypatch.setattr(loop, "_load_context", _no_context)
    monkeypatch.setattr(loop, "_build_chat_messages", _no_messages)

    structure = {"title": "T", "totalSlides": 1, "slides": [{"slideIndex": 0}]}
    layout0 = {"layoutId": "1", "layoutName": "Content"}
    slide0 = {"slideIndex": 0, "layoutId": "1", "layoutName": "Content", "contents": []}

    cleared = {"count": 0}

    script = [
        [{"id": "1", "function": {"name": "plan_presentation_structure", "arguments": "{}"}}],
        [{"id": "2", "function": {"name": "select_layout", "arguments": json.dumps({"content_type": "content", "slide_index": 0})}}],
        [{"id": "3", "function": {"name": "design_slide_content", "arguments": json.dumps({"slide_index": 0})}}],
        [{"id": "4", "function": {"name": "finalize_and_render_pptx", "arguments": json.dumps({"session_id": str(session_id)})}}],
        [{"id": "5", "function": {"name": "validate_rendered_output", "arguments": json.dumps({"pptx_path": "/x.pptx", "session_id": str(session_id)})}}],
        [],
    ]
    calls = {"n": 0}

    async def _chat(*a, **k):
        idx = calls["n"]
        calls["n"] += 1
        tool_calls = script[idx] if idx < len(script) else []
        return {"message": {"content": "" if tool_calls else "Done."}, "tool_calls": tool_calls}

    monkeypatch.setattr(loop, "_chat_with_retry", _chat)
    loop.MAX_ITERATIONS = 10

    async def _execute_tool(name, args):
        if name == "plan_presentation_structure":
            return ToolResult(success=True, data=structure)
        if name == "select_layout":
            return ToolResult(success=True, data=layout0)
        if name == "design_slide_content":
            return ToolResult(success=True, data=slide0)
        if name == "finalize_and_render_pptx":
            return ToolResult(success=True, data={"filePath": "/x.pptx", "fileName": "x.pptx", "previewUrls": []})
        if name == "validate_rendered_output":
            return ToolResult(success=True, data={"sessionId": str(session_id), "valid": True, "issueCount": 0, "issues": []})
        raise AssertionError(f"unexpected tool {name}")

    monkeypatch.setattr(loop, "_execute_tool", _execute_tool)

    original_clear = loop._clear_working_state

    async def _tracking_clear(*a, **k):
        cleared["count"] += 1
        await original_clear(*a, **k)

    monkeypatch.setattr(loop, "_clear_working_state", _tracking_clear)

    async for _ in loop.run(session_id):
        pass

    assert cleared["count"] == 1  # only once, after validation completed


@pytest.mark.asyncio
async def test_redesign_after_a_validation_issue_then_clean_revalidation(monkeypatch):
    """The realistic auto-fix path: validate finds an overflow issue on
    slide 0, the LLM redesigns it and re-renders, then validate reports
    clean and the run is allowed to finish."""
    session_id = uuid.uuid4()
    loop = _loop()
    loop.redis_client = _FakeRedis()

    async def _no_context(*a, **k):
        return {}

    async def _no_messages(*a, **k):
        return []

    monkeypatch.setattr(loop, "_load_context", _no_context)
    monkeypatch.setattr(loop, "_build_chat_messages", _no_messages)

    structure = {"title": "T", "totalSlides": 1, "slides": [{"slideIndex": 0}]}
    layout0 = {"layoutId": "1", "layoutName": "Content"}
    slide0 = {"slideIndex": 0, "layoutId": "1", "layoutName": "Content", "contents": []}

    script = [
        [{"id": "1", "function": {"name": "plan_presentation_structure", "arguments": "{}"}}],
        [{"id": "2", "function": {"name": "select_layout", "arguments": json.dumps({"content_type": "content", "slide_index": 0})}}],
        [{"id": "3", "function": {"name": "design_slide_content", "arguments": json.dumps({"slide_index": 0})}}],
        [{"id": "4", "function": {"name": "finalize_and_render_pptx", "arguments": json.dumps({"session_id": str(session_id)})}}],
        [{"id": "5", "function": {"name": "validate_rendered_output", "arguments": json.dumps({"pptx_path": "/x.pptx", "session_id": str(session_id)})}}],
        # Issue found -> LLM redesigns and re-renders.
        [{"id": "6", "function": {"name": "design_slide_content", "arguments": json.dumps({"slide_index": 0})}}],
        [{"id": "7", "function": {"name": "finalize_and_render_pptx", "arguments": json.dumps({"session_id": str(session_id)})}}],
        [{"id": "8", "function": {"name": "validate_rendered_output", "arguments": json.dumps({"pptx_path": "/x.pptx", "session_id": str(session_id)})}}],
        [],
    ]
    calls = {"n": 0}
    validate_calls = {"n": 0}

    async def _chat(*a, **k):
        idx = calls["n"]
        calls["n"] += 1
        tool_calls = script[idx] if idx < len(script) else []
        return {"message": {"content": "" if tool_calls else "Done."}, "tool_calls": tool_calls}

    monkeypatch.setattr(loop, "_chat_with_retry", _chat)
    loop.MAX_ITERATIONS = 15

    async def _execute_tool(name, args):
        if name == "plan_presentation_structure":
            return ToolResult(success=True, data=structure)
        if name == "select_layout":
            return ToolResult(success=True, data=layout0)
        if name == "design_slide_content":
            return ToolResult(success=True, data=slide0)
        if name == "finalize_and_render_pptx":
            return ToolResult(success=True, data={"filePath": "/x.pptx", "fileName": "x.pptx", "previewUrls": []})
        if name == "validate_rendered_output":
            validate_calls["n"] += 1
            if validate_calls["n"] == 1:
                return ToolResult(success=True, data={
                    "sessionId": str(session_id), "valid": False, "issueCount": 1,
                    "issues": [{"slideIndex": 0, "type": "overflow", "detail": "Text extends past the slide boundary"}],
                })
            return ToolResult(success=True, data={"sessionId": str(session_id), "valid": True, "issueCount": 0, "issues": []})
        raise AssertionError(f"unexpected tool {name}")

    monkeypatch.setattr(loop, "_execute_tool", _execute_tool)

    async for _ in loop.run(session_id):
        pass

    assert validate_calls["n"] == 2
    assert loop.working_state.validation_attempts == 2


# ── slide previews are held back until the deck is settled ────────────────


class TestDeferredSlidePreviews:
    """
    ``slide_finalized`` carries the preview image the UI displays. It used to
    be emitted the moment finalize_and_render_pptx returned - i.e. *before*
    validate_rendered_output had run. Any slide validation then sent back for
    redesign had already been shown to the user as finished, and changed
    underneath them afterwards.
    """

    @staticmethod
    def _stage(loop, urls=("/previews/s/slide_0.png", "/previews/s/slide_1.png")):
        loop.working_state.accumulated_slides = [
            {"slideIndex": 0, "title": "one"},
            {"slideIndex": 1, "title": "two"},
        ]
        loop.working_state.pending_slide_previews = [
            {"slideIndex": i, "slideData": None, "previewUrl": u}
            for i, u in enumerate(urls)
        ]
        loop.working_state.previews_published = False

    def test_flush_emits_one_slide_finalized_per_staged_preview(self):
        loop = _loop()
        self._stage(loop)

        events = loop._flush_slide_previews()

        assert [e["type"] for e in events] == ["slide_finalized", "slide_finalized"]
        assert [e["slideIndex"] for e in events] == [0, 1]
        assert events[0]["previewUrl"] == "/previews/s/slide_0.png"

    def test_flush_is_idempotent(self):
        """The validation path and the loop-exit safety net can both call
        this; the second must not double-publish."""
        loop = _loop()
        self._stage(loop)

        assert len(loop._flush_slide_previews()) == 2
        assert loop._flush_slide_previews() == []

    def test_a_rerender_supersedes_an_already_published_set(self):
        loop = _loop()
        self._stage(loop)
        loop._flush_slide_previews()

        # A validation-driven redesign re-renders, staging fresh previews.
        self._stage(loop, urls=("/previews/s/slide_0.png?v=2",))

        events = loop._flush_slide_previews()
        assert len(events) == 1
        assert events[0]["previewUrl"].endswith("?v=2")

    def test_nothing_is_published_when_no_render_has_happened(self):
        assert _loop()._flush_slide_previews() == []

    @pytest.mark.asyncio
    async def test_staged_previews_survive_a_working_state_round_trip(self):
        """An ask_user_question pause checkpoints to Redis mid-run; previews
        staged but not yet validated must not be lost on resume."""
        session_id = uuid.uuid4()
        loop = _loop()
        loop.redis_client = _FakeRedis()
        self._stage(loop)

        await loop._save_working_state(session_id)

        resumed = _loop()
        resumed.redis_client = loop.redis_client
        await resumed._load_working_state(session_id)

        assert resumed.working_state.previews_published is False
        events = resumed._flush_slide_previews()
        assert [e["slideIndex"] for e in events] == [0, 1]


# ── a deck with known defects cannot be declared finished ─────────────────


class TestValidationMustBeCleanBeforeCompleting:
    """
    The exit gate used to accept "validate_rendered_output has run", not
    "the rendered file is actually clean". A deck whose chart hung off the
    page was reported to the model, ignored, and then announced as complete
    and offered for download.
    """

    @staticmethod
    def _validated(issues):
        loop = _loop()
        ws = loop.working_state
        ws.has_called_finalize = True
        ws.has_called_validate = True
        ws.validation_attempts = 1
        ws.validation_issues = issues
        ws.validation_clean = not issues
        return loop

    def test_a_clean_validation_satisfies_the_gate(self):
        ws = self._validated([]).working_state
        satisfied = ws.validation_clean or ws.validation_attempts >= ws.MAX_VALIDATION_ATTEMPTS
        assert satisfied is True

    def test_a_validation_that_found_issues_does_not_satisfy_the_gate(self):
        ws = self._validated([
            {"slideIndex": 2, "type": "overflow", "detail": "A chart runs off the slide"},
        ]).working_state
        satisfied = ws.validation_clean or ws.validation_attempts >= ws.MAX_VALIDATION_ATTEMPTS
        assert satisfied is False

    def test_the_retry_cap_still_releases_the_gate(self):
        """An unfixable defect must not trap the run forever."""
        loop = self._validated([{"slideIndex": 0, "type": "overflow", "detail": "x"}])
        ws = loop.working_state
        ws.validation_attempts = ws.MAX_VALIDATION_ATTEMPTS
        satisfied = ws.validation_clean or ws.validation_attempts >= ws.MAX_VALIDATION_ATTEMPTS
        assert satisfied is True

    def test_the_issue_summary_names_slides_by_their_display_number(self):
        loop = self._validated([
            {"slideIndex": 0, "type": "overflow", "detail": "A chart runs off the slide"},
            {"slideIndex": 3, "type": "overlap", "detail": "A chart overlaps the text"},
        ])
        summary = loop._describe_validation_issues()
        assert "Slide 1" in summary and "Slide 4" in summary
        assert "runs off the slide" in summary

    def test_the_issue_summary_is_capped(self):
        loop = self._validated([
            {"slideIndex": i, "type": "overflow", "detail": f"issue {i}"} for i in range(20)
        ])
        summary = loop._describe_validation_issues(limit=5)
        assert summary.count("\n") == 5  # 5 issues + the "...and N more" line
        assert "15 more" in summary


class TestDownloadIsHeldUntilTheDeckIsSettled:
    """The download card and the file's sources entry are driven by
    file_generated, which api/generate.py now emits on the loop's
    deck_finalized event rather than on the finalize tool_result."""

    @staticmethod
    def _rendered(loop, clean: bool = True):
        loop.working_state.pending_slide_previews = [
            {"slideIndex": 0, "slideData": None, "previewUrl": "/previews/s/slide_0.png"},
        ]
        loop.working_state.previews_published = False
        loop.working_state.pending_render_result = {
            "filePath": "/app/output/deck.pptx", "fileName": "deck.pptx",
            "fileSizeBytes": 1234, "slideCount": 1,
        }
        loop.working_state.validation_clean = clean
        loop.working_state.validation_issues = [] if clean else [
            {"slideIndex": 2, "type": "overflow",
             "detail": "A chart or image runs off the slide - past the bottom by 61pt."},
        ]

    def test_deck_finalized_carries_the_render_result(self):
        loop = _loop()
        self._rendered(loop)

        events = loop._flush_slide_previews()
        deck = next(e for e in events if e["type"] == "deck_finalized")
        assert deck["renderResult"]["fileName"] == "deck.pptx"
        assert deck["validationClean"] is True

    def test_no_deck_finalized_before_the_deck_settles(self):
        """Staging a render must not, by itself, publish anything."""
        loop = _loop()
        self._rendered(loop)
        assert loop.working_state.previews_published is False

    def test_a_rerender_republishes_the_newer_file(self):
        loop = _loop()
        self._rendered(loop)
        loop._flush_slide_previews()

        self._rendered(loop)
        loop.working_state.pending_render_result["fileName"] = "deck-v2.pptx"

        deck = next(e for e in loop._flush_slide_previews() if e["type"] == "deck_finalized")
        assert deck["renderResult"]["fileName"] == "deck-v2.pptx"

    @pytest.mark.asyncio
    async def test_the_staged_render_result_survives_a_pause(self):
        session_id = uuid.uuid4()
        loop = _loop()
        loop.redis_client = _FakeRedis()
        self._rendered(loop)
        await loop._save_working_state(session_id)

        resumed = _loop()
        resumed.redis_client = loop.redis_client
        await resumed._load_working_state(session_id)

        events = resumed._flush_slide_previews()
        deck = next(e for e in events if e["type"] == "deck_finalized")
        assert deck["renderResult"]["filePath"] == "/app/output/deck.pptx"


class TestOnlyTheBrokenSlidesGetRedesigned:
    """
    A deck where one chart overflows should cost one slide redesign, not a
    regeneration of every slide. Redesigning slides that already render
    correctly spends an LLM call each and risks rewriting copy the user was
    happy with, to fix a problem those slides do not have.
    """

    @staticmethod
    def _deck(loop, slide_count=5):
        ws = loop.working_state
        ws.presentation_structure = {"totalSlides": slide_count}
        for i in range(slide_count):
            ws.add_slide({"slideIndex": i, "contents": []})
            ws.selected_layouts[i] = {"layoutId": "content"}
        return ws

    @staticmethod
    def _overflow(slide_index):
        return {"slideIndex": slide_index, "type": "overflow",
                "detail": "A chart or image runs off the slide - past the bottom by 77pt."}

    def test_a_single_bad_slide_targets_only_that_slide(self):
        loop = _loop()
        ws = self._deck(loop)
        assert ws.compute_slides_needing_revision([self._overflow(3)]) == [3]

    def test_redesigning_an_unaffected_slide_is_blocked(self):
        loop = _loop()
        ws = self._deck(loop)
        ws.slides_needing_revision = [3]

        error = loop._check_precondition("design_slide_content", {"slide_index": 1})

        assert error is not None
        assert "does not need" in error
        assert "slide(s) 4" in error  # 1-based in user-facing text

    def test_redesigning_the_flagged_slide_is_allowed(self):
        loop = _loop()
        ws = self._deck(loop)
        ws.slides_needing_revision = [3]

        assert loop._check_precondition("design_slide_content", {"slide_index": 3}) is None

    def test_authoring_a_brand_new_slide_is_still_allowed(self):
        """Moving an oversized chart onto its own slide is a fix we actively
        suggest, so an index that has not been designed yet must not be
        blocked."""
        loop = _loop()
        ws = self._deck(loop)
        ws.slides_needing_revision = [3]
        ws.selected_layouts[5] = {"layoutId": "content"}

        assert loop._check_precondition("design_slide_content", {"slide_index": 5}) is None

    def test_a_mostly_broken_deck_falls_back_to_a_full_rewrite(self):
        """Past a certain share, steering slide-by-slide edits is not cheaper
        than just redesigning - an empty list means no restriction."""
        loop = _loop()
        ws = self._deck(loop)
        issues = [self._overflow(i) for i in range(4)]  # 4 of 5

        assert ws.compute_slides_needing_revision(issues) == []

    def test_no_restriction_applies_when_nothing_was_flagged(self):
        loop = _loop()
        ws = self._deck(loop)
        ws.slides_needing_revision = []

        assert loop._check_precondition("design_slide_content", {"slide_index": 2}) is None

    def test_a_clean_validation_lifts_the_restriction(self):
        loop = _loop()
        ws = self._deck(loop)
        ws.slides_needing_revision = [3]

        assert ws.compute_slides_needing_revision([]) == []

    def test_duplicate_issues_on_one_slide_collapse(self):
        loop = _loop()
        ws = self._deck(loop)
        issues = [self._overflow(3), self._overflow(3)]

        assert ws.compute_slides_needing_revision(issues) == [3]

    def test_issues_without_a_slide_index_are_ignored(self):
        loop = _loop()
        ws = self._deck(loop)
        issues = [{"type": "aesthetic", "detail": "deck-wide note"}, self._overflow(2)]

        assert ws.compute_slides_needing_revision(issues) == [2]

    @pytest.mark.asyncio
    async def test_the_revision_target_survives_a_pause(self):
        session_id = uuid.uuid4()
        loop = _loop()
        loop.redis_client = _FakeRedis()
        self._deck(loop)
        loop.working_state.slides_needing_revision = [3]
        await loop._save_working_state(session_id)

        resumed = _loop()
        resumed.redis_client = loop.redis_client
        await resumed._load_working_state(session_id)

        assert resumed.working_state.slides_needing_revision == [3]


class TestInjectedDesignSystemIsNarrowedPerTool:
    """
    The whole design system used to be serialised into every tool call. For
    design_slide_content that meant the full layout catalog - every
    placeholder's EMU geometry for every layout - riding along on each slide,
    in the arguments AND again in the echoed result, despite that tool only
    reading the fonts and the palette. On a token-per-minute budget that is
    what tips a normal deck into rate-limit territory partway through.
    """

    @staticmethod
    def _design_system():
        from app.design_system import get_default_design_system
        return get_default_design_system()

    def test_slide_content_gets_only_fonts_and_palette(self):
        from app.agent.loop import _project_design_system

        projected = _project_design_system("design_slide_content", self._design_system())

        assert set(projected) == {"typography", "palette"}
        assert set(projected["typography"]) == {"titleFont", "bodyFont"}
        assert "layouts" not in projected

    def test_slide_content_keeps_only_the_swatches_the_prompt_renders(self):
        from app.agent.loop import _project_design_system

        projected = _project_design_system("design_slide_content", self._design_system())

        assert len(projected["palette"]["colors"]) <= 5

    def test_the_planner_gets_layout_names_without_the_geometry(self):
        from app.agent.loop import _project_design_system

        projected = _project_design_system(
            "plan_presentation_structure", self._design_system()
        )

        assert set(projected) == {"layouts"}
        for layout in projected["layouts"]:
            assert set(layout) == {"layoutId", "layoutName"}

    def test_the_projection_is_substantially_smaller(self):
        from app.agent.loop import _project_design_system

        design_system = self._design_system()
        full = len(json.dumps(design_system))
        for tool in ("design_slide_content", "plan_presentation_structure"):
            projected = len(json.dumps(_project_design_system(tool, design_system)))
            assert projected < full / 4, f"{tool} payload barely shrank"

    def test_other_tools_are_unaffected(self):
        """select_layout genuinely needs the placeholder geometry."""
        from app.agent.loop import _project_design_system

        design_system = self._design_system()
        assert _project_design_system("select_layout", design_system) == design_system

    def test_an_empty_design_system_stays_empty(self):
        from app.agent.loop import _project_design_system

        assert _project_design_system("design_slide_content", {}) == {}
