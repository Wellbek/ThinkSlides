"""
A run records why it did what it did, what it spent, and why it stopped.

Without this, every question about a run - why it took seven minutes, which
step spent the tokens, why validation gave up - was answered by reading logs
and guessing. The trace is a span tree per run: phases, tool calls and model
calls, each with a reason, a duration and its tokens, and the run's
termination on the root.
"""

import json

import pytest

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
