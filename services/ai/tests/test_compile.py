"""
Slide compile (#323): one answer per slide, checked like a drafted slide.

The whole-run behaviour - every slide asked for at once, one retry on a
failed check - is in test_agent_loop_workflow.py. These hold the rules about
a single answer: where its exhibits go, what it may call, and when compile
is the path at all.
"""

import json
import uuid

import pytest

from app.agent import compile as slide_compile
from app.agent import pipeline
from app.agent.loop import AgentLoop
from app.agent.registry import ToolResult


def _loop(pack_id="authored-abc") -> AgentLoop:
    loop = AgentLoop(db=None, mode="simple")
    loop.context = {}
    loop.working_state.pack_id = pack_id
    loop.working_state.presentation_structure = {
        "totalSlides": 2, "slides": [{"title": "One"}, {"title": "Two"}]}
    return loop


def _call(name, args, call_id="c1"):
    return {"id": call_id, "function": {"name": name, "arguments": json.dumps(args)}}


async def _apply(loop, index, calls):
    seen = []

    async def _execute(name, args):
        seen.append((name, args))
        if name == "design_slide_content":
            return ToolResult(success=True, data={**args["written"], "contents": [{}]})
        return ToolResult(success=True, data={"slideIndex": args.get("slide_index"),
                                              "content": {"type": "table"}})

    loop._execute_tool = _execute
    result = None
    async for kind, payload in slide_compile._apply(
        loop, index, calls, {}, uuid.uuid4(), pipeline.PHASES_BY_NAME["compile"]):
        if kind == "result":
            result = payload
    return seen, result


@pytest.mark.asyncio
async def test_an_exhibit_goes_on_the_slide_that_was_asked_for():
    loop = _loop()
    seen, (_, failures) = await _apply(loop, 1, [
        _call("build_table", {"slide_index": 0, "columns": ["A"], "rows": [["x"]]}),
        _call("write_slide", {"slide": {"contents": []}}, "c2"),
    ])

    built = next(args for name, args in seen if name == "build_table")
    assert built["slide_index"] == 1
    written = next(args for name, args in seen if name == "design_slide_content")
    assert written["slide_index"] == 1 and written["written"]["slideIndex"] == 1
    assert not [f for f in failures if not f.startswith("the slide check")]


@pytest.mark.asyncio
async def test_a_tool_compile_does_not_offer_is_refused_not_run():
    loop = _loop()
    seen, (_, failures) = await _apply(loop, 0, [
        _call("select_layout", {"slide_index": 0}),
        _call("write_slide", {"slide": {"contents": []}}, "c2"),
    ])

    assert "select_layout" not in [name for name, _ in seen]
    assert any(f.startswith("select_layout: select_layout is not offered") for f in failures)


@pytest.mark.asyncio
async def test_an_answer_with_no_copy_fails_and_writes_nothing():
    loop = _loop()
    seen, (_, failures) = await _apply(loop, 0, [
        _call("build_table", {"columns": ["A"], "rows": [["x"]]})])

    assert "design_slide_content" not in [name for name, _ in seen]
    assert failures == ["write_slide was not called, so the slide has no copy."]


def test_compile_is_not_the_path_on_an_uploaded_template():
    assert slide_compile.applies(_loop(pack_id=None)) is False
    assert slide_compile.applies(_loop()) is True


def test_one_slide_sent_back_with_a_script_on_offer_stays_in_the_conversation(monkeypatch):
    """Where drafting would offer edit_deck_with_code, a position fix may be
    the right answer, and compile cannot give it."""
    monkeypatch.setattr(AgentLoop, "code_amendment_enabled", property(lambda self: True))
    loop = _loop()
    ws = loop.working_state
    ws.has_called_finalize = True
    ws.validated_serial = ws.design_serial

    ws.revision_queue = [1]
    assert slide_compile.applies(loop) is False
    ws.revision_queue = [0, 1]
    assert slide_compile.applies(loop) is True


def test_a_slide_compile_tried_this_round_is_left_to_drafting():
    loop = _loop()
    assert slide_compile.outstanding(loop) == [0, 1]
    loop.working_state.compile_tried = {0}
    assert slide_compile.outstanding(loop) == [1]
