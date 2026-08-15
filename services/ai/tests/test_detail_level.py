"""
Tests for the Concise / Detailed / Comprehensive content-depth control.

Mirrors test_agent_mode.py's structure for the effort-level (mode) plumbing,
since detail_level follows the exact same None-means-inherit-on-resume
convention and Redis round trip.
"""

import json
import uuid

import pytest

from app.agent.loop import (
    DETAIL_LEVEL_COMPREHENSIVE,
    DETAIL_LEVEL_CONCISE,
    DETAIL_LEVEL_DETAILED,
    MODE_SIMPLE,
    AgentLoop,
    normalize_detail_level,
)


def _loop(mode: str | None = MODE_SIMPLE, detail_level: str | None = None) -> AgentLoop:
    loop = AgentLoop(db=None, mode=mode, detail_level=detail_level)
    # These tests drive planning and drafting directly, so they stand in for a
    # run whose user opted out of the consultation checkpoints - which are
    # covered in test_agent_loop_workflow.py.
    loop.questions_declined = True
    return loop


class _FakeRedis:
    def __init__(self, initial: dict | None = None):
        self.store = dict(initial or {})

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, key):
        self.store.pop(key, None)


# ── normalize_detail_level ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        (DETAIL_LEVEL_CONCISE, DETAIL_LEVEL_CONCISE),
        (DETAIL_LEVEL_DETAILED, DETAIL_LEVEL_DETAILED),
        (DETAIL_LEVEL_COMPREHENSIVE, DETAIL_LEVEL_COMPREHENSIVE),
        ("", DETAIL_LEVEL_DETAILED),
        (None, DETAIL_LEVEL_DETAILED),
        ("extreme", DETAIL_LEVEL_DETAILED),
        ("Concise", DETAIL_LEVEL_DETAILED),
    ],
)
def test_normalize_detail_level_falls_back_to_detailed(value, expected):
    assert normalize_detail_level(value) == expected


# ── Resolution and persistence ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_configure_defaults_to_detailed_when_none_was_given():
    loop = _loop(detail_level=None)

    await loop._configure()

    assert loop.detail_level == DETAIL_LEVEL_DETAILED


@pytest.mark.asyncio
async def test_an_explicit_detail_level_is_kept():
    loop = _loop(detail_level=DETAIL_LEVEL_COMPREHENSIVE)

    await loop._configure()

    assert loop.detail_level == DETAIL_LEVEL_COMPREHENSIVE


@pytest.mark.asyncio
async def test_detail_level_is_saved_into_the_working_state():
    session_id = uuid.uuid4()
    loop = _loop(detail_level=DETAIL_LEVEL_CONCISE)
    loop.redis_client = _FakeRedis()

    await loop._save_working_state(session_id)

    saved = json.loads(loop.redis_client.store[f"working_state:{session_id}"])
    assert saved["detail_level"] == DETAIL_LEVEL_CONCISE


@pytest.mark.asyncio
async def test_a_resumed_run_inherits_the_paused_run_s_detail_level():
    session_id = uuid.uuid4()
    state = json.dumps({"detail_level": DETAIL_LEVEL_COMPREHENSIVE, "completed_tools": {}})

    loop = _loop(detail_level=None)  # None = "inherit"
    loop.redis_client = _FakeRedis({f"working_state:{session_id}": state})

    assert await loop._load_working_state(session_id) is True
    assert loop.detail_level == DETAIL_LEVEL_COMPREHENSIVE


@pytest.mark.asyncio
async def test_an_explicit_detail_level_wins_over_a_stale_working_state():
    session_id = uuid.uuid4()
    state = json.dumps({"detail_level": DETAIL_LEVEL_COMPREHENSIVE, "completed_tools": {}})

    loop = _loop(detail_level=DETAIL_LEVEL_CONCISE)
    loop.redis_client = _FakeRedis({f"working_state:{session_id}": state})

    await loop._load_working_state(session_id)

    assert loop.detail_level == DETAIL_LEVEL_CONCISE


@pytest.mark.asyncio
async def test_working_state_written_before_detail_levels_existed_resumes_as_detailed():
    session_id = uuid.uuid4()
    state = json.dumps({"completed_tools": {}})

    loop = _loop(detail_level=None)
    loop.redis_client = _FakeRedis({f"working_state:{session_id}": state})

    await loop._load_working_state(session_id)

    assert loop.detail_level == DETAIL_LEVEL_DETAILED


def test_system_prompt_names_the_active_detail_level():
    loop = _loop(detail_level=DETAIL_LEVEL_COMPREHENSIVE)
    loop.detail_level = DETAIL_LEVEL_COMPREHENSIVE  # bypass _configure for this unit test

    prompt = loop._build_system_prompt({})

    assert "comprehensive" in prompt.lower()


def test_system_prompt_defaults_gracefully_when_built_before_configure():
    """_build_system_prompt is sometimes called directly in tests without
    _configure() ever running - detail_level must not leak as the string
    "None" into the prompt."""
    loop = _loop(detail_level=None)

    prompt = loop._build_system_prompt({})

    assert "None" not in prompt


# ── The loop injects detail_level into tool args server-side ───────────


async def _dispatch(loop, name, args):
    """Run one tool call through the loop's dispatcher, which is where the
    composer's chosen depth is attached to the calls that read it."""
    from app.agent import pipeline

    result = None
    async for kind, payload in loop._dispatch_tool(
        name, args, "call-1",
        context={}, session_id=uuid.uuid4(),
        current_phase=pipeline.PHASES_BY_NAME["draft"].label,
    ):
        if kind != "event":
            result = payload
    return result


@pytest.mark.asyncio
async def test_loop_injects_detail_level_into_plan_and_design_calls(monkeypatch):
    from app.agent.registry import ToolResult

    loop = _loop(detail_level=DETAIL_LEVEL_COMPREHENSIVE)
    captured: dict = {}

    async def _execute_tool(name, args):
        captured.update(args)
        return ToolResult(success=True, data={"title": "T", "totalSlides": 1, "slides": []})

    monkeypatch.setattr(loop, "_execute_tool", _execute_tool)

    await _dispatch(loop, "plan_presentation_structure", {})

    assert captured["detail_level"] == DETAIL_LEVEL_COMPREHENSIVE


@pytest.mark.asyncio
async def test_loop_injects_detail_level_into_analyze_document_and_extract_key_data(monkeypatch):
    from app.agent.registry import ToolResult

    loop = _loop(detail_level=DETAIL_LEVEL_COMPREHENSIVE)
    loop._session_has_documents = True   # or extract_key_data has nothing to mine
    captured: list[dict] = []

    async def _execute_tool(name, args):
        captured.append(dict(args))
        return ToolResult(success=True, data={})

    monkeypatch.setattr(loop, "_execute_tool", _execute_tool)

    await _dispatch(loop, "analyze_document", {"document_path": "/x.pdf"})
    await _dispatch(loop, "extract_key_data", {"content": "text"})

    assert [c["detail_level"] for c in captured] == [
        DETAIL_LEVEL_COMPREHENSIVE, DETAIL_LEVEL_COMPREHENSIVE,
    ]



# ── Tool prompts vary by detail_level ───────────────────────────────────


@pytest.mark.asyncio
async def test_plan_presentation_structure_prompt_varies_by_detail_level():
    from app.agent import run_context
    from app.agent.tools.plan_presentation_structure import PlanPresentationStructureTool

    captured_prompts = []

    class _Capture:
        async def chat(self, messages, tools=None):
            captured_prompts.append(messages[0]["content"])
            return {"message": {"content": '{"title": "T", "totalSlides": 1, "slides": []}'}}

    token = run_context.set_chat_provider(_Capture())
    try:
        await PlanPresentationStructureTool.run(content_summary="x", detail_level="comprehensive")
        await PlanPresentationStructureTool.run(content_summary="x", detail_level="concise")
    finally:
        run_context.reset_chat_provider(token)

    assert "5-8 key points" in captured_prompts[0]
    assert "5-8 key points" not in captured_prompts[1]
    assert captured_prompts[0] != captured_prompts[1]


@pytest.mark.asyncio
async def test_design_slide_content_prompt_varies_by_detail_level():
    from app.agent import run_context
    from app.agent.tools.design_slide_content import DesignSlideContentTool

    captured_prompts = []

    class _Capture:
        async def chat(self, messages, tools=None):
            captured_prompts.append(messages[0]["content"])
            return {"message": {"content": '{"slideIndex": 0, "contents": []}'}}

    token = run_context.set_chat_provider(_Capture())
    try:
        await DesignSlideContentTool.run(
            slide_outline="{}", layout_info="{}", slide_index=0, detail_level="comprehensive"
        )
        await DesignSlideContentTool.run(
            slide_outline="{}", layout_info="{}", slide_index=0, detail_level="concise"
        )
    finally:
        run_context.reset_chat_provider(token)

    assert "thorough paragraph" in captured_prompts[0]
    assert "thorough paragraph" not in captured_prompts[1]
    assert captured_prompts[0] != captured_prompts[1]


@pytest.mark.asyncio
async def test_extract_key_data_prompt_varies_by_detail_level():
    from app.agent import run_context
    from app.agent.tools.extract_key_data import ExtractKeyDataTool

    captured_prompts = []

    class _Capture:
        async def chat(self, messages, tools=None):
            captured_prompts.append(messages[0]["content"])
            return {"message": {"content": "{}"}}

    token = run_context.set_chat_provider(_Capture())
    try:
        await ExtractKeyDataTool.run(content="x", detail_level="comprehensive")
        await ExtractKeyDataTool.run(content="x", detail_level="concise")
    finally:
        run_context.reset_chat_provider(token)

    assert "up to 20 each" in captured_prompts[0]
    assert "up to 20 each" not in captured_prompts[1]
    assert captured_prompts[0] != captured_prompts[1]


@pytest.mark.asyncio
async def test_extract_key_data_widens_the_content_window_at_higher_detail():
    from app.agent import run_context
    from app.agent.tools.extract_key_data import ExtractKeyDataTool

    captured_prompts = []

    class _Capture:
        async def chat(self, messages, tools=None):
            captured_prompts.append(messages[0]["content"])
            return {"message": {"content": "{}"}}

    long_content = "word " * 6000  # well beyond the "concise" 8000-char ceiling
    token = run_context.set_chat_provider(_Capture())
    try:
        await ExtractKeyDataTool.run(content=long_content, detail_level="comprehensive")
        await ExtractKeyDataTool.run(content=long_content, detail_level="concise")
    finally:
        run_context.reset_chat_provider(token)

    assert len(captured_prompts[0]) > len(captured_prompts[1])


def test_plan_presentation_structure_comprehensive_guidance_mentions_table_and_chart():
    """Comprehensive-detail planning should actually propose real tables/
    charts where the data supports them, now that Stages C/D make those
    content types render for real instead of being schema-legal no-ops."""
    from app.agent.tools.plan_presentation_structure import _DETAIL_GUIDANCE

    comprehensive = _DETAIL_GUIDANCE["comprehensive"]
    assert "table" in comprehensive.lower()
    assert "chart" in comprehensive.lower()
