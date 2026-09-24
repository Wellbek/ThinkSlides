"""
A paid run is bounded before it starts and while it runs.

Live runs bill an API against a small, fixed budget. These hold the three
promises that make that safe: a run is refused if its cap exceeds what is
left, stopped once it spends its cap, and what it spent is written down.
"""

import json
from types import SimpleNamespace

import pytest

from app.llm import usage
from app.llm.provider import LLMProvider
from app.research.budget import (
    BudgetExceeded,
    Ledger,
    Meter,
    MeteredProvider,
    Price,
    load_prices,
    projected_cost,
)

LUNA = Price(input=0.20, cached_input=0.02, output=1.20)


class _Spender(LLMProvider):
    """A provider whose every call spends a fixed number of tokens."""

    model = "gpt-5.6-luna"

    def __init__(self, input_tokens=100_000, output_tokens=10_000):
        self.input_tokens, self.output_tokens, self.calls = input_tokens, output_tokens, 0

    async def chat(self, messages, tools=None, temperature=0.7, max_tokens=4096):
        self.calls += 1
        usage.record("openai", self.model, SimpleNamespace(usage=SimpleNamespace(
            prompt_tokens=self.input_tokens, completion_tokens=self.output_tokens)))
        return {"message": {"content": "ok"}, "tool_calls": []}


@pytest.fixture
def benchmark(tmp_path):
    (tmp_path / "budget.json").write_text(json.dumps({"limitUsd": 3.0}))
    (tmp_path / "prices.json").write_text(json.dumps({"models": {
        "gpt-5.6-luna": {"input": 0.20, "cachedInput": 0.02, "output": 1.20}}}))
    return tmp_path


class TestPrices:
    def test_cached_tokens_are_charged_at_the_cached_rate(self):
        # 1M in of which 400k cached, 100k out.
        assert LUNA.cost(1_000_000, 400_000, 100_000) == pytest.approx(0.12 + 0.008 + 0.12)

    def test_the_table_is_read_by_model(self, benchmark):
        assert load_prices(benchmark / "prices.json")["gpt-5.6-luna"] == LUNA


class TestTheLedger:
    def test_a_cap_larger_than_what_is_left_is_refused(self, benchmark):
        ledger = Ledger(benchmark / "ledger.jsonl", benchmark / "budget.json")
        ledger.record(task="t", costUsd=2.5)
        with pytest.raises(BudgetExceeded, match=r"\$0\.50 of \$3\.00"):
            ledger.authorise(1.0)
        ledger.authorise(0.4)

    def test_a_run_without_a_cap_is_refused(self, benchmark):
        with pytest.raises(BudgetExceeded):
            Ledger(benchmark / "ledger.jsonl", benchmark / "budget.json").authorise(0)


class TestTheMeter:
    @pytest.mark.asyncio
    async def test_a_run_stops_once_it_has_spent_its_cap(self):
        # Each call: 100k in, 10k out at luna prices = $0.032.
        meter = Meter(LUNA, cap=0.05)
        provider = MeteredProvider(_Spender(), meter)
        token = usage.start()
        try:
            await provider.chat([])
            await provider.chat([])   # starts under the cap, ends over it
            with pytest.raises(BudgetExceeded, match="cap is"):
                await provider.chat([])
        finally:
            usage.reset(token)
        assert provider.inner.calls == 2
        assert meter.summary()["costUsd"] == pytest.approx(0.064)

    @pytest.mark.asyncio
    async def test_spend_carries_across_the_parts_of_a_run(self):
        """Each part of a run starts its own usage counter; the meter must not."""
        meter = Meter(LUNA, cap=1.0)
        provider = MeteredProvider(_Spender(), meter)
        for _ in range(2):
            token = usage.start()
            try:
                await provider.chat([])
            finally:
                usage.reset(token)
        assert meter.summary()["inputTokens"] == 200_000


def test_a_recorded_run_projects_its_cost_from_its_traces(tmp_path):
    spans = [
        {"kind": "llm", "usage": {"inputTokens": 150_000, "cachedInputTokens": 0, "outputTokens": 7_000}},
        {"kind": "tool", "usage": {"inputTokens": 999, "cachedInputTokens": 0, "outputTokens": 999}},
    ]
    (tmp_path / "trace-0.jsonl").write_text("\n".join(json.dumps(s) for s in spans))
    assert projected_cost(tmp_path, LUNA) == pytest.approx(0.03 + 0.0084)
