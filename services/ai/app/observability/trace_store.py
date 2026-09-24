"""
Where a finished trace goes.

``DatabaseSink`` is the product's record: every run's spans in ``run_spans``,
next to the ``agent_events`` the UI replays. ``JsonlSink`` is for headless
runs, which keep everything about a run in one directory.

``render_timeline`` turns a trace into the indented, human-readable view a
developer reads first; the JSONL is what tools read.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.observability.trace import Span, Trace


def _uuid(value: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None


class DatabaseSink:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def write(self, trace: Trace) -> None:
        from app.db.models import RunSpan

        session_id = _uuid(trace.root.attrs.get("sessionId"))
        for span in trace.spans:
            self.db.add(RunSpan(
                id=uuid.UUID(span.span_id),
                run_id=uuid.UUID(span.run_id),
                session_id=session_id,
                parent_id=_uuid(span.parent_id),
                kind=span.kind,
                name=span.name[:100],
                reason=span.reason,
                status=span.status,
                error=span.error or None,
                started_at=span.started_at,
                ended_at=span.ended_at,
                duration_ms=span.duration_ms,
                input_tokens=span.input_tokens,
                cached_input_tokens=span.cached_input_tokens,
                output_tokens=span.output_tokens,
                attrs=json.loads(json.dumps(span.attrs, default=str)),
            ))
        await self.db.commit()


class JsonlSink:
    """``trace.jsonl`` (one span per line) and ``trace.md`` in a run directory."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    async def write(self, trace: Trace) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        with open(self.directory / "trace.jsonl", "w") as f:
            for span in trace.spans:
                f.write(json.dumps(span.to_dict(), default=str) + "\n")
        (self.directory / "trace.md").write_text(render_timeline(trace))


def render_timeline(trace: Trace) -> str:
    """The span tree as indented lines: time, tokens, reason, outcome."""
    children: dict[str | None, list[Span]] = {}
    for span in trace.spans:
        if span is not trace.root:
            children.setdefault(span.parent_id, []).append(span)

    root = trace.root
    totals = trace.totals()
    lines = [
        f"# Run {root.run_id}",
        "",
        f"- Termination: **{root.attrs.get('termination', '?')}**",
        f"- Duration: {root.duration_ms / 1000:.1f} s",
        f"- Model calls: {totals['llmCalls']}; tokens in {totals['inputTokens']} "
        f"(cached {totals['cachedInputTokens']}), out {totals['outputTokens']}",
        "",
        "```",
    ]

    def walk(span: Span, depth: int) -> None:
        tokens = ""
        if span.input_tokens or span.output_tokens:
            tokens = f"  [{span.input_tokens}/{span.cached_input_tokens}c -> {span.output_tokens}]"
        status = "" if span.status == "ok" else f"  <{span.status}>"
        reason = f"  - {span.reason}" if span.reason and span.kind != "llm" else ""
        lines.append(
            f"{span.duration_ms / 1000:7.2f}s {'  ' * depth}{span.kind}:{span.name}"
            f"{tokens}{status}{reason}"
        )
        for child in children.get(span.span_id, []):
            walk(child, depth + 1)

    for child in children.get(root.span_id, []):
        walk(child, 0)
    lines.append("```")
    return "\n".join(lines) + "\n"
