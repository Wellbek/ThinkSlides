"""
Where a headless run's model calls go, and how they are kept.

Three ways to answer a model call without a workspace's saved provider:

- ``ClaudeCodeProvider`` writes each request to a file and waits for a
  response file. A Claude Code session reads the request and answers as the
  model, so development runs cost a subscription rather than API credits.
  Its answers are a frontier model's, never a cheap model's.
- ``RecordingProvider`` passes calls to a real provider and keeps the answers.
- ``ReplayProvider`` answers from a kept cassette, in order, at no cost.

Every answer, however it was produced, is appended to the run's
``cassette.jsonl``, so any run can be replayed. Search results are kept the
same way (see ``recorded_search``), since a replay that searched again would
not be a replay.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
from pathlib import Path
from types import SimpleNamespace

from app.llm import usage
from app.llm.provider import LLMProvider, VisionLLMProvider
from app.observability import trace

# Characters per token for estimating what a request would cost on an API
# model. Prompts here are English prose mixed with JSON, which tokenises at
# roughly 3.5-4 characters per token; the lower end keeps estimates on the
# expensive side. The one paid anchor run measures the real ratio.
CHARS_PER_TOKEN = 3.5

POLL_SECONDS = 0.5


def request_sha(payload) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]


def vision_sha(prompt: str, images) -> str:
    """A vision request's hash, the pictures included.

    Every page of a deck is judged with the same prompt, so a hash of the
    prompt alone is the same for all of them, and answers recorded as pages
    were judged concurrently replayed onto the wrong pages.
    """
    digests = [hashlib.sha256(str(image).encode()).hexdigest()[:16] for image in images or []]
    return request_sha({"prompt": prompt, "images": digests})


def estimated_usage(request_text: str, response_text: str) -> SimpleNamespace:
    return SimpleNamespace(usage=SimpleNamespace(
        prompt_tokens=int(len(request_text) / CHARS_PER_TOKEN),
        completion_tokens=int(len(response_text) / CHARS_PER_TOKEN),
    ))


def _where() -> str:
    """The phase and tool a call was made from, read off the open spans."""
    t, current = trace.current_trace(), trace.current_span()
    if t is None or current is None:
        return ""
    by_id = {s.span_id: s for s in t.spans}
    names = []
    while current is not None and current is not t.root:
        # A step names which of a phase's concurrent calls this is, such as
        # the slide a compile request is for (#323).
        if current.kind in ("phase", "step", "tool"):
            names.append(f"{current.kind}:{current.name}")
        current = by_id.get(current.parent_id)
    return " > ".join(reversed(names))


def to_loop_response(answer: dict) -> dict:
    """``{content, tool_calls: [{name, arguments}]}`` in the shape the loop reads."""
    calls = []
    for i, call in enumerate(answer.get("tool_calls") or []):
        args = call.get("arguments") or {}
        calls.append({
            "id": call.get("id") or f"call_{i}",
            "type": "function",
            "function": {
                "name": call["name"],
                "arguments": args if isinstance(args, str) else json.dumps(args),
            },
        })
    message = {"role": "assistant", "content": answer.get("content") or ""}
    return {"message": message, "tool_calls": calls}


class Cassette:
    """An append-only record of every answer a run received, in order."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.entries: list[dict] = []
        if path.exists():
            self.entries = [json.loads(line) for line in path.read_text().splitlines() if line]

    def append(self, kind: str, sha: str, response, where: str = "") -> None:
        entry = {"seq": len(self.entries) + 1, "kind": kind, "sha": sha, "response": response}
        if where:
            # Where in the run the call came from, which names the slide a
            # compile request is for, so replay can tell concurrent calls
            # apart when the prompt itself has drifted (a new day's date).
            entry["where"] = where
        self.entries.append(entry)
        with open(self.path, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")


class ClaudeCodeExchange:
    """The request/response folder shared by a run and whoever answers it."""

    def __init__(self, directory: Path, cassette: Cassette, *, timeout_s: float = 3600) -> None:
        self.directory = directory
        self.cassette = cassette
        self.timeout_s = timeout_s
        directory.mkdir(parents=True, exist_ok=True)
        # Numbered after anything already here. A folder can be reused - a
        # deck graded twice - and a request numbered from 1 again would find
        # the old answer waiting and take it without anyone being asked.
        self.seq = max((int(p.name[:4]) for p in directory.glob("[0-9][0-9][0-9][0-9].request.json")),
                       default=0)

    async def ask(self, kind: str, request: dict, readable: str,
                  sha: str | None = None) -> dict:
        self.seq += 1
        stem = f"{self.seq:04d}"
        request = {"seq": self.seq, "kind": kind, "where": _where(), **request}
        (self.directory / f"{stem}.request.json").write_text(json.dumps(request, indent=2, default=str))
        (self.directory / f"{stem}.request.md").write_text(readable)
        # Written last: its appearance is the signal that the request is complete.
        (self.directory / f"{stem}.pending").touch()

        answer_path = self.directory / f"{stem}.response.json"
        deadline = time.monotonic() + self.timeout_s
        while True:
            if answer_path.exists():
                try:
                    answer = json.loads(answer_path.read_text())
                    break
                except json.JSONDecodeError:
                    pass  # still being written
            if time.monotonic() > deadline:
                raise TimeoutError(f"no answer to request {stem} after {self.timeout_s:.0f}s")
            await asyncio.sleep(POLL_SECONDS)
        (self.directory / f"{stem}.pending").unlink(missing_ok=True)
        self.cassette.append(kind, sha or request_sha(request.get("messages") or request.get("prompt")),
                             answer, where=request.get("where") or "")
        return answer


def _render_chat_request(seq: int, where: str, messages: list[dict], tools: list[dict] | None) -> str:
    lines = [
        f"# Request {seq:04d} - chat",
        "",
        f"Called from: {where or 'outside any phase'}",
        "",
        f"Answer in `{seq:04d}.response.json` as "
        '`{"content": "<text>", "tool_calls": [{"name": "<tool>", "arguments": {...}}]}`.',
        "Leave `tool_calls` empty to answer in text only.",
        "",
    ]
    for i, m in enumerate(messages):
        role = m.get("role", "?")
        lines.append(f"## {i + 1}. {role}" + (f" ({m.get('tool_call_id')})" if m.get("tool_call_id") else ""))
        lines.append("")
        content = m.get("content")
        lines.append(content if isinstance(content, str) else json.dumps(content, indent=2, default=str))
        for call in m.get("tool_calls") or []:
            fn = call.get("function", {})
            lines.append(f"\n-> tool call `{fn.get('name')}` {fn.get('arguments')}")
        lines.append("")
    if tools:
        lines += ["## Tools offered", ""]
        for tool in tools:
            fn = tool.get("function", tool)
            params = fn.get("parameters", {}).get("properties", {})
            required = set(fn.get("parameters", {}).get("required", []))
            lines.append(f"### `{fn.get('name')}`")
            lines.append(fn.get("description", ""))
            for name, spec in params.items():
                star = "" if name in required else " (optional)"
                lines.append(f"- `{name}`{star}: {spec.get('type', '')} - {spec.get('description', '')}")
            lines.append("")
    return "\n".join(lines)


class ClaudeCodeProvider(LLMProvider):
    model = "claude-code"
    # Stands in for a frontier API model, which caches prefixes; a run here
    # takes the shape a live run would.
    prefix_cache = True

    def __init__(self, exchange: ClaudeCodeExchange) -> None:
        self.exchange = exchange

    async def chat(self, messages: list[dict], tools: list[dict] | None = None,
                   temperature: float = 0.7, max_tokens: int = 4096) -> dict:
        seq = self.exchange.seq + 1
        readable = _render_chat_request(seq, _where(), messages, tools)
        answer = await self.exchange.ask(
            "chat", {"messages": messages, "tools": tools or []}, readable,
        )
        request_text = json.dumps(messages, default=str) + json.dumps(tools or [], default=str)
        usage.record("claude-code", self.model, estimated_usage(request_text, json.dumps(answer)))
        span = trace.current_span()
        if span is not None:
            span.set(tokensEstimated=True, exchange=f"{seq:04d}")
        return to_loop_response(answer)


class ClaudeCodeVisionProvider(VisionLLMProvider):
    model = "claude-code"

    def __init__(self, exchange: ClaudeCodeExchange) -> None:
        self.exchange = exchange

    async def chat_with_images(self, prompt: str, images: list[str],
                               temperature: float = 0.4, max_tokens: int = 4096) -> str:
        seq = self.exchange.seq + 1
        paths = []
        for i, image in enumerate(images, 1):
            data = image.split(",", 1)[1] if image.startswith("data:") else image
            path = self.exchange.directory / f"{seq:04d}-{i}.png"
            path.write_bytes(base64.b64decode(data))
            paths.append(path.name)
        readable = "\n".join([
            f"# Request {seq:04d} - vision",
            "",
            f"Called from: {_where() or 'outside any phase'}",
            "",
            f"Images: {', '.join(paths)}",
            "",
            f'Answer in `{seq:04d}.response.json` as `{{"content": "<text>"}}`.',
            "",
            prompt,
        ])
        answer = await self.exchange.ask("vision", {"prompt": prompt, "images": paths}, readable,
                                         sha=vision_sha(prompt, images))
        # An image costs roughly a fixed block of tokens; 1,000 per page is the
        # conservative end for a slide-sized render.
        usage.record("claude-code", self.model, estimated_usage(
            prompt + "x" * int(1000 * CHARS_PER_TOKEN * len(paths)), answer.get("content") or ""))
        return answer.get("content") or ""


class RecordingProvider(LLMProvider):
    """A real provider whose every answer is kept for replay."""

    def __init__(self, inner: LLMProvider, cassette: Cassette) -> None:
        self.inner = inner
        self.cassette = cassette
        self.model = getattr(inner, "model", "unknown")

    async def chat(self, messages, tools=None, temperature=0.7, max_tokens=4096) -> dict:
        result = await self.inner.chat(messages, tools=tools, temperature=temperature,
                                       max_tokens=max_tokens)
        answer = {
            "content": (result.get("message") or {}).get("content") or "",
            "tool_calls": [
                {"id": c.get("id"), "name": c["function"]["name"],
                 "arguments": c["function"].get("arguments") or "{}"}
                for c in result.get("tool_calls") or []
            ],
        }
        self.cassette.append("chat", request_sha(messages), answer, where=_where())
        return result


class RecordingVisionProvider(VisionLLMProvider):
    def __init__(self, inner: VisionLLMProvider, cassette: Cassette) -> None:
        self.inner = inner
        self.cassette = cassette
        self.model = getattr(inner, "model", "unknown")

    async def chat_with_images(self, prompt, images, *args, **kwargs) -> str:
        text = await self.inner.chat_with_images(prompt, images, *args, **kwargs)
        self.cassette.append("vision", vision_sha(prompt, images), {"content": text},
                             where=_where())
        return text


class _Tape:
    """A cassette's answers of one kind, handed to the requests that asked them.

    An answer goes to the request it was recorded for: the earliest unused
    entry whose request hash matches. When none matches - the prompt drifted
    since recording, which every prompt does on another day, since each
    carries the date - the earliest unused entry recorded from the same place
    in the run stands in, and only then the earliest unused entry of all,
    the order a sequential run asked in. Order alone is not enough once
    calls run concurrently: slides compiled in parallel are recorded in the
    order their answers arrived, not the order they were asked (#323).
    """

    def __init__(self, cassette: Cassette, kind: str) -> None:
        self.entries = [e for e in cassette.entries if e["kind"] == kind]
        self.kind = kind
        self.used: set[int] = set()

    def next(self, sha: str, where: str = "") -> dict:
        unused = [i for i in range(len(self.entries)) if i not in self.used]
        if not unused:
            raise LookupError(
                f"the cassette has no {self.kind} answer #{len(self.used) + 1}: "
                "the run asked for more than was recorded"
            )
        position = next(
            (i for i in unused if self.entries[i]["sha"] == sha),
            next((i for i in unused if where and self.entries[i].get("where") == where),
                 unused[0]))
        self.used.add(position)
        entry = self.entries[position]
        span = trace.current_span()
        if span is not None:
            span.set(replayed=entry["seq"], replayDrift=entry["sha"] != sha)
        return entry["response"]


class ReplayProvider(LLMProvider):
    model = "replay"
    # Replays a run that took the shape a caching provider gives it.
    prefix_cache = True

    def __init__(self, cassette: Cassette) -> None:
        self.tape = _Tape(cassette, "chat")

    async def chat(self, messages, tools=None, temperature=0.7, max_tokens=4096) -> dict:
        # Yield before answering, as a real provider does: calls a phase sends
        # together are then all in flight before the first is answered, which
        # is what the prefix ledger needs to see to count them as concurrent.
        await asyncio.sleep(0)
        return to_loop_response(self.tape.next(request_sha(messages), _where()))


class ReplayVisionProvider(VisionLLMProvider):
    model = "replay"

    def __init__(self, cassette: Cassette) -> None:
        self.tape = _Tape(cassette, "vision")

    async def chat_with_images(self, prompt, images, *args, **kwargs) -> str:
        return self.tape.next(vision_sha(prompt, images), _where()).get("content") or ""


class RecordedSearchError(RuntimeError):
    """A search that failed when it was recorded, failing again on replay."""


def recorded_search(search, cassette: Cassette, *, replay: bool):
    """Wrap ``run_search`` so its results are kept, or served back on replay.

    A failed search is kept too, as its error. Leaving it out would hand the
    next recorded result to this call on replay, and every search after it
    would answer the question before.
    """
    tape = _Tape(cassette, "search") if replay else None

    async def run_search(config, query, **kwargs):
        sha = request_sha({"query": query, **kwargs})
        if tape is not None:
            kept = tape.next(sha)
            if isinstance(kept, dict) and "error" in kept:
                raise RecordedSearchError(kept["error"])
            return kept
        try:
            results = await search(config, query, **kwargs)
        except Exception as e:
            cassette.append("search", sha, {"error": f"{type(e).__name__}: {e}"})
            raise
        cassette.append("search", sha, results)
        return results

    return run_search
