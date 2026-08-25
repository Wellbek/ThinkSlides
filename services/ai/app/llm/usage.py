"""
What a run actually spent.

Nothing counted tokens, which made every question about cost unanswerable and
every claim about it a guess. Routing the hard work to a stronger model is
only worth doing if the trade can be seen, and "this run cost more than it
used to" is not a thing anyone should have to take on trust.

Counted, not priced. A token count is a fact the provider returns; a price is
a number that changes without warning and would be stale in the repository
within a month. The count is reported per model, so the cost of a run is a
multiplication anyone can do against whatever the current rates are.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field


@dataclass
class ModelUsage:
    """What one model was asked to do during a run."""

    provider: str
    model: str
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class RunUsage:
    """Every model a run used, and how much of each."""

    by_model: dict[tuple[str, str], ModelUsage] = field(default_factory=dict)

    def record(self, provider: str, model: str, *, input_tokens: int = 0,
               output_tokens: int = 0) -> None:
        key = (provider, model)
        entry = self.by_model.get(key)
        if entry is None:
            entry = ModelUsage(provider=provider, model=model)
            self.by_model[key] = entry
        entry.calls += 1
        entry.input_tokens += max(0, int(input_tokens or 0))
        entry.output_tokens += max(0, int(output_tokens or 0))

    @property
    def calls(self) -> int:
        return sum(entry.calls for entry in self.by_model.values())

    @property
    def total_tokens(self) -> int:
        return sum(entry.total_tokens for entry in self.by_model.values())

    def to_dict(self) -> dict:
        return {
            "calls": self.calls,
            "totalTokens": self.total_tokens,
            "models": [
                {
                    "provider": entry.provider,
                    "model": entry.model,
                    "calls": entry.calls,
                    "inputTokens": entry.input_tokens,
                    "outputTokens": entry.output_tokens,
                }
                for entry in sorted(
                    self.by_model.values(),
                    key=lambda e: -e.total_tokens,
                )
            ],
        }


# Per-run, for the same reason every other run-scoped thing is a ContextVar:
# one process serves several organisations at once, and a module global would
# bill one workspace's run to another.
_usage: ContextVar[RunUsage | None] = ContextVar("thinkslides_usage", default=None)


def start() -> Token:
    """Begin counting for one run."""
    return _usage.set(RunUsage())


def current() -> RunUsage | None:
    """What this run has spent so far, or None outside a counted run."""
    return _usage.get()


def reset(token: Token) -> None:
    _usage.reset(token)


def record(provider: str, model: str, response: object) -> None:
    """Count one call, from whatever shape the provider reports usage in.

    Silent when there is no counter installed and when a provider reports
    nothing: a missing count is a gap in what we know, never a reason to fail
    a generation that has otherwise succeeded.
    """
    run = _usage.get()
    if run is None:
        return
    usage = getattr(response, "usage", None)
    if usage is None:
        run.record(provider, model)
        return
    # OpenAI-shaped and Anthropic-shaped, which name the same two numbers
    # differently and are the only two shapes in this codebase.
    input_tokens = (getattr(usage, "prompt_tokens", None)
                    or getattr(usage, "input_tokens", None) or 0)
    output_tokens = (getattr(usage, "completion_tokens", None)
                     or getattr(usage, "output_tokens", None) or 0)
    run.record(provider, model, input_tokens=input_tokens,
               output_tokens=output_tokens)
