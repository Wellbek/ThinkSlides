"""
A run records why it did what it did, what it spent, and why it stopped.

Without this, every question about a run - why it took seven minutes, which
step spent the tokens, why validation gave up - was answered by reading logs
and guessing. The trace is a span tree per run: phases, tool calls and model
calls, each with a reason, a duration and its tokens, and the run's
termination on the root.
"""

import json
from types import SimpleNamespace

import pytest

from app.llm import usage
from app.llm.provider import LLMProvider
from app.llm.traced import TracedChatProvider, prompt_sha
from app.observability import trace
from app.observability.trace_store import JsonlSink, render_timeline


class ListSink:
    def __init__(self):
        self.traces = []

    async def write(self, t):
        self.traces.append(t)


class BrokenSink:
    async def write(self, t):
        raise RuntimeError("database is down")


class TestSpans:
    @pytest.mark.asyncio
    async def test_spans_nest_under_whatever_is_current(self):
        t = trace.begin("run")
        with trace.span("phase", "draft") as phase:
            with trace.span("tool", "design_slide_content") as tool:
                pass
        await trace.finish(t, [], termination=trace.COMPLETE)

        assert phase.parent_id == t.root.span_id
        assert tool.parent_id == phase.span_id
        assert tool.ended_at is not None

    @pytest.mark.asyncio
    async def test_tokens_land_on_the_call_that_spent_them(self):
        t = trace.begin("run")
        with trace.span("tool", "plan"):
            with trace.span("llm", "chat") as call:
                trace.record_usage(input_tokens=900, cached_input_tokens=600, output_tokens=40)
        await trace.finish(t, [], termination=trace.COMPLETE)

        assert (call.input_tokens, call.cached_input_tokens, call.output_tokens) == (900, 600, 40)
        assert t.root.attrs["llmCalls"] == 1
        assert t.root.attrs["cachedInputTokens"] == 600

    @pytest.mark.asyncio
    async def test_a_failing_block_marks_its_span_and_restores_the_parent(self):
        t = trace.begin("run")
        with pytest.raises(ValueError):
            with trace.span("tool", "render") as tool:
                raise ValueError("no deck")
        assert trace.current_span() is t.root
        await trace.finish(t, [], termination=trace.ERROR)

        assert tool.status == "error"
        assert "no deck" in tool.error

    @pytest.mark.asyncio
    async def test_the_termination_is_recorded_and_every_sink_gets_the_trace(self):
        sink = ListSink()
        t = trace.begin("run", sessionId="s")
        await trace.finish(t, [BrokenSink(), sink], termination=trace.PAUSED)

        # A sink that fails must not take the others, or the run, down with it.
        assert sink.traces == [t]
        assert t.root.attrs["termination"] == trace.PAUSED
        assert trace.current_trace() is None

    def test_outside_a_run_nothing_is_recorded_and_nothing_breaks(self):
        with trace.span("tool", "x") as s:
            trace.record_usage(input_tokens=5)
        assert s is None


class TestTokenCounts:
    def test_openai_chat_reports_cached_as_part_of_the_prompt(self):
        response = SimpleNamespace(usage=SimpleNamespace(
            prompt_tokens=1000, completion_tokens=50,
            prompt_tokens_details=SimpleNamespace(cached_tokens=768)))
        assert usage.counts(response) == (1000, 768, 50)

    def test_the_responses_api_nests_it_one_level_differently(self):
        response = SimpleNamespace(usage=SimpleNamespace(
            input_tokens=400, output_tokens=20,
            input_tokens_details=SimpleNamespace(cached_tokens=0)))
        assert usage.counts(response) == (400, 0, 20)

    def test_anthropic_input_excludes_the_cache_so_it_is_added_back(self):
        response = SimpleNamespace(usage=SimpleNamespace(
            input_tokens=100, output_tokens=30,
            cache_read_input_tokens=5000, cache_creation_input_tokens=200))
        assert usage.counts(response) == (5300, 5000, 30)

    def test_a_response_without_usage_counts_as_nothing(self):
        assert usage.counts(SimpleNamespace()) == (0, 0, 0)


class _ScriptedProvider(LLMProvider):
    model = "cheap-1"

    async def chat(self, messages, tools=None, temperature=0.7, max_tokens=4096):
        usage.record("scripted", self.model, SimpleNamespace(usage=SimpleNamespace(
            prompt_tokens=120, completion_tokens=8)))
        return {"message": {"content": ""},
                "tool_calls": [{"function": {"name": "build_chart"}}]}


class TestTracedProvider:
    @pytest.mark.asyncio
    async def test_every_call_is_a_span_with_what_was_asked_and_spent(self):
        t = trace.begin("run")
        provider = TracedChatProvider(_ScriptedProvider())
        messages = [{"role": "user", "content": "hi"}]
        await provider.chat(messages, tools=[{"type": "function"}])
        await trace.finish(t, [], termination=trace.COMPLETE)

        call = next(s for s in t.spans if s.kind == "llm")
        assert call.attrs["model"] == "cheap-1"
        assert call.attrs["tools"] == 1
        assert call.attrs["toolCalls"] == ["build_chart"]
        assert call.attrs["promptSha"] == prompt_sha(messages)
        assert (call.input_tokens, call.output_tokens) == (120, 8)

    def test_it_still_looks_like_the_provider_it_wraps(self):
        assert TracedChatProvider(_ScriptedProvider()).model == "cheap-1"


class TestSinks:
    @pytest.mark.asyncio
    async def test_a_run_directory_gets_the_spans_and_a_readable_timeline(self, tmp_path):
        t = trace.begin("run")
        with trace.span("phase", "plan", reason="first phase that applies"):
            with trace.span("llm", "chat"):
                trace.record_usage(input_tokens=10, output_tokens=2)
        await trace.finish(t, [JsonlSink(tmp_path)], termination=trace.COMPLETE)

        spans = [json.loads(line) for line in (tmp_path / "trace.jsonl").read_text().splitlines()]
        assert [s["kind"] for s in spans] == ["run", "phase", "llm"]
        timeline = (tmp_path / "trace.md").read_text()
        assert "Termination: **complete**" in timeline
        assert "phase:plan" in timeline and "first phase that applies" in timeline

    def test_the_timeline_indents_children_under_parents(self):
        t = trace.Trace(run_id="r", root=trace.Span(kind="run", name="run", run_id="r"))
        phase = trace.Span(kind="phase", name="draft", run_id="r", parent_id=t.root.span_id)
        tool = trace.Span(kind="tool", name="build_chart", run_id="r", parent_id=phase.span_id)
        t.spans += [phase, tool]
        lines = render_timeline(t).splitlines()
        draft = next(line for line in lines if "phase:draft" in line)
        chart = next(line for line in lines if "tool:build_chart" in line)
        assert chart.index("tool:") > draft.index("phase:")


class TestARunExplainsItself:
    @pytest.mark.asyncio
    async def test_a_paused_run_says_it_paused_and_where(self, monkeypatch):
        from tests.test_pipeline import INTAKE, _loop, _text

        loop, session_id, _ = _loop([_text(INTAKE)], monkeypatch=monkeypatch)
        sink = ListSink()
        loop.trace_sinks = [sink]

        [e async for e in loop.run(session_id)]

        (t,) = sink.traces
        assert t.root.attrs["termination"] == trace.PAUSED
        phases = [s for s in t.spans if s.kind == "phase" and s.status != "skipped"]
        assert [p.name for p in phases][:1] == ["intake"]
        assert any(s.kind == "tool" and s.name == "web_search"
                   and s.reason == "called by the loop" for s in t.spans)

    @pytest.mark.asyncio
    async def test_a_finished_run_records_validation_and_every_phase_it_skipped(self, monkeypatch):
        from tests.test_pipeline import INTAKE, TOOL_RESULTS, _calls, _loop, _text

        loop, session_id, _ = _loop(
            [_calls(("select_layout", {"slide_index": 0}),
                    ("design_slide_content", {"slide_index": 0})),
             _calls(("select_layout", {"slide_index": 1}),
                    ("design_slide_content", {"slide_index": 1})),
             _text("Done.")],
            state={
                "brief": json.loads(INTAKE), "scouted": True,
                "consulted_before_planning": True, "autonomy_confirmed": True,
                "reviewed_outline_with_user": True, "offered_directions": True,
                "presentation_structure": TOOL_RESULTS["plan_presentation_structure"],
                "design_system": {"palette": {}, "layouts": []},
                "web_search_calls": 9,
                "researched_urls": [f"https://s/{i}" for i in range(8)],
                "mode": "deep", "detail_level": "detailed",
            },
            messages=[{"role": "user", "content": "Go ahead as planned"}],
            monkeypatch=monkeypatch,
        )
        sink = ListSink()
        loop.trace_sinks = [sink]

        [e async for e in loop.run(session_id)]

        (t,) = sink.traces
        assert t.root.attrs["termination"] == trace.COMPLETE
        validate = next(s for s in t.spans if s.kind == "phase" and s.name == "validate")
        assert validate.attrs["clean"] is True and validate.attrs["issues"] == 0
        skipped = {s.name: s.reason for s in t.spans if s.status == "skipped"}
        assert skipped["intake"] == "already satisfied"
        assert "amend" in skipped
        drafted = [s for s in t.spans if s.kind == "tool" and s.name == "design_slide_content"]
        assert {s.reason for s in drafted} == {"called by the model"}


class _FailingSession:
    def __init__(self, log):
        self.log = log

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.log.append("closed")
        return False

    def add_all(self, rows):
        self.log.append(("add_all", len(rows)))

    async def commit(self):
        raise RuntimeError('relation "run_spans" does not exist')


class TestTracingNeverHurtsTheRun:
    @pytest.mark.asyncio
    async def test_the_database_sink_writes_through_a_session_of_its_own(self):
        """The API saves the reply on the run's session after the loop returns.
        A failed insert on that session left it needing a rollback, and a run
        that had built its deck was then reported as an error."""
        from app.observability.trace_store import DatabaseSink

        log = []
        t = trace.begin("run", sessionId="not-a-uuid")
        with trace.span("phase", "plan"):
            pass
        await trace.finish(t, [DatabaseSink(lambda: _FailingSession(log))],
                           termination=trace.COMPLETE)

        assert log == [("add_all", 2), "closed"]
        assert trace.current_trace() is None

    @pytest.mark.asyncio
    async def test_a_cancelled_trace_write_still_unwinds_the_run_context(self, monkeypatch):
        import asyncio

        from app.agent import run_context
        from tests.test_pipeline import INTAKE, _loop, _text

        class CancellingSink:
            async def write(self, t):
                raise asyncio.CancelledError()

        loop, session_id, _ = _loop([_text(INTAKE)], monkeypatch=monkeypatch)
        loop.trace_sinks = [CancellingSink()]

        with pytest.raises(asyncio.CancelledError):
            [e async for e in loop.run(session_id)]

        assert loop._design_system_token is None
        assert run_context.get_design_system() is None
        assert trace.current_trace() is None


class TestAVerdictIsNotABreakage:
    """A validator turning a draft down ran to the end and did its job. The
    trace said "failed" for it, the same word as a tool that broke, and every
    run's checks read seven rejected drafts as seven broken calls (#345)."""

    @staticmethod
    async def _traced(name, args):
        from app.agent.loop import AgentLoop
        from tests.test_agent_loop_workflow import _dispatch

        t = trace.begin("run")
        _, result = await _dispatch(AgentLoop(db=None), name, args)
        await trace.finish(t, [], termination=trace.COMPLETE)
        return result, next(s for s in t.spans if s.kind == "tool")

    @pytest.mark.asyncio
    async def test_a_rejected_draft_is_traced_as_rejected(self):
        result, span = await self._traced(
            "validate_slide_json", {"slide_json": json.dumps({"slideIndex": 0})})
        assert result.success is False and result.rejected is True
        assert span.status == "rejected"

    @pytest.mark.asyncio
    async def test_a_tool_that_returned_an_error_is_still_failed(self):
        result, span = await self._traced("build_table", {"slide_index": 0})
        assert result.success is False and result.rejected is False
        assert span.status == "failed"

    @pytest.mark.asyncio
    async def test_a_call_the_validator_could_not_read_is_failed(self):
        """An array or unparseable JSON is the model misusing the tool: there
        was no draft to turn down."""
        result, span = await self._traced(
            "validate_slide_json", {"slide_json": json.dumps([{"slideIndex": 0}])})
        assert result.success is False and result.rejected is False
        assert span.status == "failed"
