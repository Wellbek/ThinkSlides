"""
What a paid run may spend, and what paid runs have spent.

Live runs bill an API, and the budget for them is small, so spending is
explicit rather than observed afterwards:

- ``benchmark/prices.json`` - a dated price table with its source. A model
  that is not in it cannot be run live: an unpriced run is an unbounded one.
- ``benchmark/budget.json`` - the total that live runs may spend.
- the ledger - one line per live run: tokens and cost. It lives outside any
  checkout (``LEDGER_PATH``), because money is spent once: a copy per branch
  or worktree would let each of them see the whole budget still unspent.

A live run declares its own cap. It is refused before it starts if the cap
exceeds what is left, and each call is refused before it is made if its worst
case would take the run past the cap - so a run never ends over it.
"""

from __future__ import annotations

import datetime
import json
import os
from dataclasses import dataclass
from pathlib import Path

from app.llm import usage
from app.llm.provider import LLMProvider, VisionLLMProvider


LEDGER_PATH = Path(os.environ.get(
    "THINKSLIDES_LEDGER", Path.home() / ".local" / "share" / "thinkslides" / "ledger.jsonl"))

# Estimating a request's input tokens from its characters: the lower end of
# English-plus-JSON, so estimates err expensive.
CHARS_PER_TOKEN = 3.5
# What one rendered page costs as vision input; the conservative end for a
# slide-sized image.
TOKENS_PER_IMAGE = 1000


class BudgetExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class Price:
    input: float          # USD per million input tokens
    cached_input: float
    output: float

    def cost(self, input_tokens: int, cached_tokens: int, output_tokens: int) -> float:
        fresh = max(0, input_tokens - cached_tokens)
        return (fresh * self.input + cached_tokens * self.cached_input
                + output_tokens * self.output) / 1_000_000


def load_prices(path: Path) -> dict[str, Price]:
    table = json.loads(path.read_text())
    return {
        model: Price(input=p["input"], cached_input=p["cachedInput"], output=p["output"])
        for model, p in table["models"].items()
    }


class Ledger:
    def __init__(self, path: Path, budget_path: Path) -> None:
        self.path = path
        self.limit = float(json.loads(budget_path.read_text())["limitUsd"])

    def entries(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line]

    def spent(self) -> float:
        return sum(float(e.get("costUsd") or 0) for e in self.entries())

    def remaining(self) -> float:
        return self.limit - self.spent()

    def authorise(self, cap: float) -> None:
        if cap <= 0:
            raise BudgetExceeded("a live run needs a positive --max-cost")
        if cap > self.remaining():
            raise BudgetExceeded(
                f"--max-cost ${cap:.2f} exceeds what is left of the budget "
                f"(${self.remaining():.2f} of ${self.limit:.2f})"
            )

    def record(self, **entry) -> None:
        entry = {"at": datetime.datetime.now(datetime.timezone.utc).isoformat(), **entry}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a") as f:
            f.write(json.dumps(entry) + "\n")


class Meter:
    """Counts a live run's spend across all its parts, and enforces its cap.

    Each part of a run starts its own usage counter (AgentLoop._configure),
    so the meter reads each call's tokens as the difference the call made to
    the counter current at the time.
    """

    def __init__(self, price: Price, cap: float) -> None:
        self.price = price
        self.cap = cap
        self.input_tokens = self.cached_tokens = self.output_tokens = self.calls = 0

    @property
    def cost(self) -> float:
        return self.price.cost(self.input_tokens, self.cached_tokens, self.output_tokens)

    @staticmethod
    def _snapshot() -> tuple[int, int, int]:
        run = usage.current()
        if run is None:
            return 0, 0, 0
        entries = run.by_model.values()
        return (sum(e.input_tokens for e in entries),
                sum(e.cached_input_tokens for e in entries),
                sum(e.output_tokens for e in entries))

    def worst_case(self, input_chars: int, max_output_tokens: int, images: int = 0) -> float:
        """The most one call could cost: its whole input uncached, its whole output budget."""
        input_tokens = int(input_chars / CHARS_PER_TOKEN) + images * TOKENS_PER_IMAGE
        return self.price.cost(input_tokens, 0, max_output_tokens)

    async def measure(self, call, *, worst_case: float):
        if self.cost + worst_case > self.cap:
            raise BudgetExceeded(
                f"run stopped at ${self.cost:.4f}: the next call could cost up to "
                f"${worst_case:.4f}, and the run's cap is ${self.cap:.2f}"
            )
        before = self._snapshot()
        result = await call()
        after = self._snapshot()
        self.calls += 1
        self.input_tokens += after[0] - before[0]
        self.cached_tokens += after[1] - before[1]
        self.output_tokens += after[2] - before[2]
        return result

    def summary(self) -> dict:
        return {"calls": self.calls, "inputTokens": self.input_tokens,
                "cachedInputTokens": self.cached_tokens, "outputTokens": self.output_tokens,
                "costUsd": round(self.cost, 6)}


class MeteredProvider(LLMProvider):
    def __init__(self, inner: LLMProvider, meter: Meter) -> None:
        self.inner = inner
        self.meter = meter
        self.model = getattr(inner, "model", "unknown")

    async def chat(self, messages, tools=None, temperature=0.7, max_tokens=4096) -> dict:
        # A provider may raise the output budget above what was asked (Anthropic
        # has a floor for thinking); the larger of the two is what can be billed.
        output = max(max_tokens, getattr(self.inner, "MIN_MAX_TOKENS", 0))
        chars = len(json.dumps(messages, default=str)) + len(json.dumps(tools or [], default=str))
        return await self.meter.measure(
            lambda: self.inner.chat(messages, tools=tools, temperature=temperature,
                                    max_tokens=max_tokens),
            worst_case=self.meter.worst_case(chars, output),
        )


class MeteredVisionProvider(VisionLLMProvider):
    def __init__(self, inner: VisionLLMProvider, meter: Meter) -> None:
        self.inner = inner
        self.meter = meter
        self.model = getattr(inner, "model", "unknown")

    async def chat_with_images(self, prompt, images, *args, **kwargs) -> str:
        output = kwargs.get("max_tokens", 4096)
        return await self.meter.measure(
            lambda: self.inner.chat_with_images(prompt, images, *args, **kwargs),
            worst_case=self.meter.worst_case(len(prompt), output, images=len(images)),
        )


def projected_cost(run_dir: Path, price: Price) -> float:
    """What a recorded run's model calls would cost at ``price``."""
    tokens = [0, 0, 0]
    for part in sorted(run_dir.glob("trace-*.jsonl")):
        for line in part.read_text().splitlines():
            span = json.loads(line)
            if span["kind"] == "llm":
                u = span["usage"]
                tokens[0] += u["inputTokens"]
                tokens[1] += u["cachedInputTokens"]
                tokens[2] += u["outputTokens"]
    return price.cost(*tokens)
