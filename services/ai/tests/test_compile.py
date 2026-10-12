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


# ── The #381 review ──────────────────────────────────────────────────────


async def _compile_one(loop, index, answers, execute):
    """Run compile_slide for one slide against scripted answers."""
    replies = list(answers)
    asked = []

    async def _chat(messages, tools=None):
        asked.append(messages)
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    loop._chat_with_retry = _chat
    loop._execute_tool = execute
    tools = slide_compile.offered_tools(loop)
    async for _ in slide_compile.compile_slide(
            loop, index, "shared", tools, {}, [], uuid.uuid4(),
            pipeline.PHASES_BY_NAME["compile"]):
        pass
    return asked


def _answer(*calls):
    return {"message": {"content": ""}, "tool_calls": list(calls)}


def _stores(loop):
    async def _execute(name, args):
        if name == "design_slide_content":
            return ToolResult(success=True, data={**args["written"], "contents": [{}]})
        if name == "validate_slide_json":
            return ToolResult(success=True, data={"valid": True})
        if name == "build_chart":
            return ToolResult(success=False, rejected=True, error="series length")
        return ToolResult(success=True, data={"slideIndex": args.get("slide_index"),
                                              "content": {"type": "table",
                                                          "slot": args.get("slot")}})
    return _execute


@pytest.mark.asyncio
async def test_a_failed_request_leaves_the_slide_to_drafting_not_the_run_ended():
    loop = _loop()
    await _compile_one(loop, 0, [RuntimeError("400 bad request")], _stores(loop))

    assert 0 in loop.working_state.compile_failures
    assert 0 not in {s.get("slideIndex") for s in loop.working_state.accumulated_slides}


@pytest.mark.asyncio
async def test_a_slide_stored_with_a_failed_exhibit_is_queued_for_drafting_with_why():
    loop = _loop()
    write = _call("write_slide", {"slide": {"contents": []}}, "w")
    chart = _call("build_chart", {"categories": ["a"]}, "c")
    await _compile_one(loop, 1, [_answer(chart, write), _answer(chart, write)], _stores(loop))

    ws = loop.working_state
    assert 1 in ws.revision_queue
    assert "  - build_chart: " in loop.slide_directive_for(1)
    assert loop.slide_directive_for(1).startswith("Finish slide 2")


@pytest.mark.asyncio
async def test_every_call_in_a_retried_answer_has_its_result_in_the_transcript():
    loop = _loop()
    no_id = {"function": {"name": "build_chart", "arguments": "{}"}}
    first = _call("write_slide", {"slide": {"contents": []}}, "w1")
    second = _call("write_slide", {"slide": {"contents": []}}, "w2")
    asked = await _compile_one(loop, 0, [_answer(no_id, first, second),
                                         _answer(_call("write_slide", {"slide": {}}, "w3"))],
                               _stores(loop))

    retry = asked[1]
    called = [c["id"] for m in retry if m["role"] == "assistant" for c in m["tool_calls"]]
    answered = [m["tool_call_id"] for m in retry if m["role"] == "tool"]
    assert sorted(called) == sorted(answered) and len(called) == 3


@pytest.mark.asyncio
async def test_a_retry_replaces_what_the_first_answer_built():
    loop = _loop()
    table = _call("build_table", {"columns": ["A"], "rows": [["x"]], "slot": "first"}, "t1")
    again = _call("build_table", {"columns": ["A"], "rows": [["x"]], "slot": "second"}, "t2")
    await _compile_one(loop, 0, [_answer(table), _answer(again, _call(
        "write_slide", {"slide": {"contents": []}}, "w"))], _stores(loop))

    assert [b["slot"] for b in loop.working_state.prepared_visuals[0]] == ["second"]


@pytest.mark.asyncio
async def test_an_image_selected_in_the_answer_is_placed_with_the_copy():
    loop = _loop()
    loop.working_state.selected_images[0] = {"data": "x", "altText": "a cat"}
    seen = []

    async def _execute(name, args):
        seen.append((name, args))
        return await _stores(loop)(name, args)

    await _compile_one(loop, 0, [_answer(_call("write_slide", {"slide": {"contents": []}}))],
                       _execute)

    written = next(a["written"] for n, a in seen if n == "design_slide_content")
    assert {"type": "image", "image": {"source": "selected"}} in written["contents"]


@pytest.mark.asyncio
async def test_replay_gives_a_drifted_concurrent_request_the_answer_from_its_own_slide(tmp_path):
    from app.observability import trace
    from app.research.exchange import Cassette, ReplayProvider

    cassette = Cassette(tmp_path / "c.jsonl")
    cassette.append("chat", "old-day", {"content": "slide 2"}, where="step:compile slide 2")
    cassette.append("chat", "old-day", {"content": "slide 1"}, where="step:compile slide 1")
    replay = ReplayProvider(Cassette(tmp_path / "c.jsonl"))

    trace.begin("r")
    try:
        with trace.span("step", "compile slide 1"):
            first = await replay.chat([{"role": "user", "content": "today"}])
    finally:
        trace._trace.set(None)
        trace._span.set(None)

    assert first["message"]["content"] == "slide 1"


class _Caching:
    """A provider that caches prefixes by itself, as OpenAI does."""
    prefix_cache = True


@pytest.mark.asyncio
async def test_the_first_slide_is_asked_alone_so_the_rest_reuse_its_cached_prefix(monkeypatch):
    """#325: every compile request shares tools, system prompt and
    conversation; sent together, none can reuse the prefix a provider caches
    while serving another. The first goes alone; the rest follow its answer."""
    import asyncio

    log = []

    async def fake_slide(loop, index, shared, tools, context, messages, session_id, phase,
                         answered=None):
        log.append(f"ask {index}")
        await asyncio.sleep(0.01)
        log.append(f"answered {index}")
        if answered is not None:
            answered.set()
        yield "event", {"slide": index}

    loop = _loop()
    loop.llm = _Caching()
    loop.working_state.presentation_structure = {"totalSlides": 3, "slides": [{}, {}, {}]}
    monkeypatch.setattr(slide_compile, "outstanding", lambda loop: [0, 1, 2])
    monkeypatch.setattr(slide_compile, "system_prompt", lambda loop, context: "S")
    monkeypatch.setattr(slide_compile, "offered_tools", lambda loop: [])
    monkeypatch.setattr(slide_compile, "compile_slide", fake_slide)
    events = [e async for e in slide_compile.compile_all(
        loop, {}, [], uuid.uuid4(), pipeline.PHASES_BY_NAME["compile"])]

    assert len(events) == 3
    assert log[:2] == ["ask 0", "answered 0"]
    # The rest then go together.
    assert log[2:4] == ["ask 1", "ask 2"]


@pytest.mark.asyncio
async def test_a_first_slide_that_fails_does_not_hold_the_others(monkeypatch):
    async def failing(loop, index, shared, tools, context, messages, session_id, phase,
                      answered=None):
        if index == 0:
            raise RuntimeError("boom")
        yield "event", {"slide": index}

    loop = _loop()
    loop.llm = _Caching()
    monkeypatch.setattr(slide_compile, "outstanding", lambda loop: [0, 1])
    monkeypatch.setattr(slide_compile, "system_prompt", lambda loop, context: "S")
    monkeypatch.setattr(slide_compile, "offered_tools", lambda loop: [])
    monkeypatch.setattr(slide_compile, "compile_slide", failing)
    events = [e async for e in slide_compile.compile_all(
        loop, {}, [], uuid.uuid4(), pipeline.PHASES_BY_NAME["compile"])]
    assert events == [("event", {"slide": 1})]


@pytest.mark.asyncio
async def test_without_a_caching_provider_every_slide_goes_at_once(monkeypatch):
    import asyncio

    log = []

    async def fake_slide(loop, index, shared, tools, context, messages, session_id, phase,
                         answered=None):
        log.append(f"ask {index}")
        await asyncio.sleep(0.01)
        log.append(f"answered {index}")
        yield "event", {"slide": index}

    loop = _loop()
    loop.llm = type("Plain", (), {"prefix_cache": False})()
    monkeypatch.setattr(slide_compile, "outstanding", lambda loop: [0, 1])
    monkeypatch.setattr(slide_compile, "system_prompt", lambda loop, context: "S")
    monkeypatch.setattr(slide_compile, "offered_tools", lambda loop: [])
    monkeypatch.setattr(slide_compile, "compile_slide", fake_slide)
    [e async for e in slide_compile.compile_all(
        loop, {}, [], uuid.uuid4(), pipeline.PHASES_BY_NAME["compile"])]
    assert log[:2] == ["ask 0", "ask 1"]


async def _first_request(loop, monkeypatch, chat, answered):
    """Run the real compile_slide for slide 0; return the order in which the
    tool dispatch after the answer saw ``answered``."""
    order = []

    async def _apply(loop, index, calls, context, session_id, phase, offered):
        order.append(f"apply, answered={answered.is_set()}")
        yield "result", ([], [])

    monkeypatch.setattr(slide_compile, "_apply", _apply)
    monkeypatch.setattr(slide_compile, "slide_request", lambda loop, index, context: "ask")
    loop._chat_with_retry = chat
    [e async for e in slide_compile.compile_slide(
        loop, 0, "S", [], {}, [], uuid.uuid4(), pipeline.PHASES_BY_NAME["compile"],
        answered=answered)]
    return order


@pytest.mark.asyncio
async def test_the_real_compile_releases_the_others_on_the_answer_not_after_the_tools(monkeypatch):
    import asyncio

    async def chat(messages, tools=None):
        return {"message": {"content": ""}, "tool_calls": []}

    answered = asyncio.Event()
    assert await _first_request(_loop(), monkeypatch, chat, answered) == [
        "apply, answered=True"]


@pytest.mark.asyncio
async def test_a_back_off_on_the_first_request_releases_the_others(monkeypatch):
    """A rate-limited first request is not in any cache, and the others must
    not wait out its retries."""
    import asyncio

    from app.llm.retry_notices import RetryNotice, publish_retry_notice

    answered = asyncio.Event()
    during_back_off = []
    loop = _loop()
    loop.NOTICE_POLL_SECONDS = 0.01

    async def chat(messages, tools=None):
        publish_retry_notice(RetryNotice(reason="rate_limit", attempt=1,
                                         max_attempts=3, delay_seconds=0.0))
        await asyncio.sleep(0.1)
        during_back_off.append(answered.is_set())
        return {"message": {"content": ""}, "tool_calls": []}

    await _first_request(loop, monkeypatch, chat, answered)
    assert during_back_off == [True]
