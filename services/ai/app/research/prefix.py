"""
How much of each model request a provider could have served from its cache.

Prompt caching (R-107) is by prefix: a request's leading tokens are cheap when
an earlier request began with exactly the same ones, in the order the
provider reads them - tools, then the messages, system first. Nothing about
a cassette records that, and a claude-code run bills nothing, so whether a
prompt is laid out to be cached could only be learned from a paid run. This
measures it on every run instead, in every mode, replays included: each chat
request is compared with every earlier one in the run, and the longest
prefix it shares with any of them is what a cache could have answered (#325).

A request is compared only with requests that had been answered before it
was sent: a provider writes its cache as it serves a request, so calls sent
together - a compile phase's slides (#323) - cannot reuse each other's
prefix, however much of it they share. The figure is still an upper bound on
what a real provider would report: it assumes the earlier request is still
in the cache, and counts a shared prefix only from ``MIN_CACHED_TOKENS`` up,
the smallest prefix the providers cache.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.llm.provider import LLMProvider

from .exchange import CHARS_PER_TOKEN, _where

# The smallest prefix OpenAI caches (Anthropic's floor is 1,024 to 4,096 by
# model; R-107). A shorter shared prefix is served at full price.
MIN_CACHED_TOKENS = 1024

LEDGER_NAME = "prefix.jsonl"


def serialised(messages: list[dict], tools: list[dict] | None) -> tuple[str, str]:
    """A request as a provider reads it for caching: its tools, then each
    message in order. Returned as (tools, the whole request)."""
    head = json.dumps(tools or [], sort_keys=True, default=str)
    return head, head + "".join(json.dumps(message, sort_keys=True, default=str)
                                for message in messages)


# Prefixes are compared a block at a time, so a long shared prefix costs a
# few comparisons of short slices rather than copies of the whole request.
_BLOCK = 4096


def common_prefix(a: str, b: str) -> int:
    """How many leading characters two strings share."""
    limit = min(len(a), len(b))
    start = 0
    while start < limit and a[start:start + _BLOCK] == b[start:start + _BLOCK]:
        start += _BLOCK
    start = min(start, limit)
    end = min(start + _BLOCK, limit)
    while start < end and a[start] == b[start]:
        start += 1
    return start


class PrefixLedger:
    """Every chat request of one run, with the prefix it shares with an
    answered one."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.sent = 0
        # Answered requests, by their tools: a request with other tools
        # shares nothing with them, since tools come first.
        self.answered: dict[str, list[str]] = {}

    def shared(self, tools_text: str, text: str) -> int:
        """The longest prefix ``text`` shares with an answered request, or
        0 below the size a provider caches."""
        shared = max((common_prefix(text, earlier)
                      for earlier in self.answered.get(tools_text, ())), default=0)
        return shared if shared / CHARS_PER_TOKEN >= MIN_CACHED_TOKENS else 0

    def enter(self, tools_text: str, text: str, shared: int, where: str) -> dict:
        """Enter a request that was answered, with what it shared when sent."""
        self.sent += 1
        self.answered.setdefault(tools_text, []).append(text)
        entry = {"seq": self.sent, "where": where, "chars": len(text), "sharedChars": shared}
        with open(self.path, "a") as handle:
            handle.write(json.dumps(entry) + "\n")
        return entry


class PrefixRecordingProvider(LLMProvider):
    """A chat provider whose every answered request is entered in the run's
    ledger."""

    def __init__(self, inner: LLMProvider, ledger: PrefixLedger) -> None:
        self.inner = inner
        self.ledger = ledger
        self.model = getattr(inner, "model", "unknown")

    async def chat(self, messages, tools=None, temperature=0.7, max_tokens=4096) -> dict:
        # What it shares is read as it is sent, against what had been
        # answered by then; it is entered once answered, so a request that
        # raised (a spent budget, a drifted tape) is not counted as input.
        tools_text, text = serialised(messages, tools)
        shared = self.ledger.shared(tools_text, text)
        where = _where()
        result = await self.inner.chat(messages, tools=tools, temperature=temperature,
                                       max_tokens=max_tokens)
        self.ledger.enter(tools_text, text, shared, where)
        return result


def summary(run_dir: Path) -> dict:
    """The run's chat input, and the share of it a prefix cache could
    answer, overall and by phase."""
    path = run_dir / LEDGER_NAME
    entries = ([json.loads(line) for line in path.read_text().splitlines() if line]
               if path.exists() else [])
    phases: dict[str, dict] = {}
    for entry in entries:
        phase = (entry.get("where") or "outside any phase").split(" > ")[0]
        bucket = phases.setdefault(phase, {"calls": 0, "chars": 0, "sharedChars": 0})
        bucket["calls"] += 1
        bucket["chars"] += entry["chars"]
        bucket["sharedChars"] += entry["sharedChars"]
    total = sum(entry["chars"] for entry in entries)
    shared = sum(entry["sharedChars"] for entry in entries)
    return {
        "calls": len(entries),
        "inputTokens": int(total / CHARS_PER_TOKEN),
        "cacheableTokens": int(shared / CHARS_PER_TOKEN),
        "cacheableShare": round(shared / total, 3) if total else 0.0,
        "byPhase": {
            phase: {"calls": bucket["calls"],
                    "inputTokens": int(bucket["chars"] / CHARS_PER_TOKEN),
                    "cacheableShare": (round(bucket["sharedChars"] / bucket["chars"], 3)
                                       if bucket["chars"] else 0.0)}
            for phase, bucket in phases.items()
        },
    }
