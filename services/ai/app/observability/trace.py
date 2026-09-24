"""
One span tree per agent run.

A run is recorded as nested spans - run, phase, tool, model call - each
carrying why it happened, how long it took, what it spent and how it ended.
The tree is what answers the questions a log line cannot: why a step ran,
why another did not, why the run stopped, and where the tokens went.

The active trace and span live in context variables, the same way the rest of
the run's context does (see app/agent/run_context.py), so tools and providers
record into the right run without being handed anything. Outside a traced run
every call here is a no-op.

Spans are collected in memory and handed to sinks once, when the run ends.
A run is a few hundred spans at most, and writing them in one batch keeps
tracing off the hot path.
"""

from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator, Protocol

from app.observability.logging import get_logger

logger = get_logger(__name__)

# How a run ended. Recorded on the root span; every exit path sets one.
COMPLETE = "complete"
PAUSED = "paused"
STALLED = "stalled"
STALLED_WITH_DECK = "stalled_with_deck"
PROVIDER_FAILED = "provider_failed"
NOT_CONFIGURED = "not_configured"
ERROR = "error"


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Span:
    kind: str
    name: str
    run_id: str
    parent_id: str | None = None
    span_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    reason: str = ""
    status: str = "ok"
    error: str = ""
    started_at: datetime = field(default_factory=_now)
    ended_at: datetime | None = None
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    attrs: dict[str, Any] = field(default_factory=dict)
    _t0: float = field(default_factory=time.perf_counter, repr=False)
    _duration_ms: int | None = field(default=None, repr=False)

    @property
    def duration_ms(self) -> int:
        if self._duration_ms is not None:
            return self._duration_ms
        return int((time.perf_counter() - self._t0) * 1000)

    def end(self, status: str | None = None, error: str = "") -> None:
        if self.ended_at is not None:
            return
        self.ended_at = _now()
        self._duration_ms = int((time.perf_counter() - self._t0) * 1000)
        if status:
            self.status = status
        if error:
            self.error = error[:2000]

    def set(self, **attrs: Any) -> None:
        self.attrs.update(attrs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "spanId": self.span_id,
            "parentId": self.parent_id,
            "runId": self.run_id,
            "kind": self.kind,
            "name": self.name,
            "reason": self.reason,
            "status": self.status,
            "error": self.error,
            "startedAt": self.started_at.isoformat(),
            "endedAt": self.ended_at.isoformat() if self.ended_at else None,
            "durationMs": self.duration_ms,
            "usage": {
                "inputTokens": self.input_tokens,
                "cachedInputTokens": self.cached_input_tokens,
                "outputTokens": self.output_tokens,
            },
            "attrs": self.attrs,
        }


@dataclass
class Trace:
    run_id: str
    root: Span
    spans: list[Span] = field(default_factory=list)

    def totals(self) -> dict[str, int]:
        """Tokens and calls summed over model-call spans only.

        Usage is recorded on the span of the call that spent it, so summing
        every span would count nothing twice - but restricting to model calls
        keeps the total meaningful if a caller ever records on a parent too.
        """
        calls = [s for s in self.spans if s.kind == "llm"]
        return {
            "llmCalls": len(calls),
            "inputTokens": sum(s.input_tokens for s in calls),
            "cachedInputTokens": sum(s.cached_input_tokens for s in calls),
            "outputTokens": sum(s.output_tokens for s in calls),
        }


class TraceSink(Protocol):
    async def write(self, trace: Trace) -> None: ...


_trace: ContextVar[Trace | None] = ContextVar("thinkslides_trace", default=None)
_span: ContextVar[Span | None] = ContextVar("thinkslides_span", default=None)


def current_trace() -> Trace | None:
    return _trace.get()


def current_span() -> Span | None:
    return _span.get()


def begin(name: str = "run", *, run_id: str | None = None, **attrs: Any) -> Trace:
    """Start tracing a run and make its root span current."""
    root = Span(kind="run", name=name, run_id=run_id or str(uuid.uuid4()), attrs=dict(attrs))
    trace = Trace(run_id=root.run_id, root=root, spans=[root])
    _trace.set(trace)
    _span.set(root)
    return trace


async def finish(trace: Trace, sinks: list[TraceSink], *, termination: str) -> None:
    """Close the run, write it to every sink, and stop tracing.

    A sink that fails is logged and skipped: losing a trace must never lose
    the run it describes.
    """
    for span in trace.spans:
        if span.ended_at is None:
            span.end(status="unfinished" if span is not trace.root else None)
    trace.root.set(termination=termination, **trace.totals())
    for sink in sinks:
        try:
            await sink.write(trace)
        except Exception as e:  # noqa: BLE001 - tracing never fails a run
            logger.warning("trace_sink_failed", sink=type(sink).__name__, error=str(e))
    _trace.set(None)
    _span.set(None)


@contextmanager
def span(kind: str, name: str, *, reason: str = "", **attrs: Any) -> Iterator[Span | None]:
    """Record a child of the current span for the duration of the block.

    The parent is restored with ``set`` rather than a token reset: spans are
    opened inside async generators, and a generator closed from another
    context would make a token reset raise.
    """
    trace = _trace.get()
    if trace is None:
        yield None
        return
    parent = _span.get()
    child = Span(
        kind=kind,
        name=name,
        run_id=trace.run_id,
        parent_id=parent.span_id if parent else trace.root.span_id,
        reason=reason,
        attrs=dict(attrs),
    )
    trace.spans.append(child)
    _span.set(child)
    try:
        yield child
    except BaseException as e:
        child.end(status="error", error=f"{type(e).__name__}: {e}")
        raise
    finally:
        child.end()
        _span.set(parent)


def event(kind: str, name: str, *, reason: str = "", status: str = "ok", **attrs: Any) -> None:
    """A zero-length span, for decisions that took no time - a skipped phase."""
    with span(kind, name, reason=reason, **attrs) as s:
        if s is not None:
            s.status = status


def record_usage(*, input_tokens: int = 0, cached_input_tokens: int = 0,
                 output_tokens: int = 0) -> None:
    """Attribute tokens to the current span, which is the model call's own."""
    current = _span.get()
    if current is None:
        return
    current.input_tokens += max(0, int(input_tokens or 0))
    current.cached_input_tokens += max(0, int(cached_input_tokens or 0))
    current.output_tokens += max(0, int(output_tokens or 0))
