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


def serialised(messages: list[dict], tools: list[dict] | None) -> str:
    """A request as a provider reads it for caching: tools, then each message
    in order."""
    parts = [json.dumps(tools or [], sort_keys=True, default=str)]
    parts.extend(json.dumps(message, sort_keys=True, default=str) for message in messages)
    return "".join(parts)


def common_prefix(a: str, b: str) -> int:
    """How many leading characters two strings share."""
    limit = min(len(a), len(b))
    if a[:limit] == b[:limit]:
        return limit
    low, high = 0, limit
    while low < high:
        middle = (low + high + 1) // 2
        if a[:middle] == b[:middle]:
            low = middle
        else:
            high = middle - 1
    return low


class PrefixLedger:
    """Every chat request of one run, with the prefix it shares with an
    earlier one."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.sent = 0
        # Requests already answered: what a cache could hold when the next
        # request is sent.
        self.answered: list[str] = []

    def record(self, messages: list[dict], tools: list[dict] | None, where: str) -> tuple[dict, str]:
        """Enter a request as it is sent. Returns its entry and its text, to
        hand to ``answered`` once the provider has replied."""
        text = serialised(messages, tools)
        shared = max((common_prefix(text, earlier) for earlier in self.answered), default=0)
        if shared / CHARS_PER_TOKEN < MIN_CACHED_TOKENS:
            shared = 0
        self.sent += 1
        entry = {"seq": self.sent, "where": where, "chars": len(text), "sharedChars": shared}
        with open(self.path, "a") as handle:
            handle.write(json.dumps(entry) + "\n")
        return entry, text

    def answered_request(self, text: str) -> None:
        self.answered.append(text)


class PrefixRecordingProvider(LLMProvider):
    """A chat provider whose every request is entered in the run's ledger."""

    def __init__(self, inner: LLMProvider, ledger: PrefixLedger) -> None:
        self.inner = inner
        self.ledger = ledger
        self.model = getattr(inner, "model", "unknown")

    async def chat(self, messages, tools=None, temperature=0.7, max_tokens=4096) -> dict:
        _, text = self.ledger.record(messages, tools, _where())
        result = await self.inner.chat(messages, tools=tools, temperature=temperature,
                                       max_tokens=max_tokens)
        self.ledger.answered_request(text)
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
