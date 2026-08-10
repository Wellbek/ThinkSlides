"""
Live visibility into the waits a provider imposes on a run.

A rate-limited generation used to be indistinguishable from a frozen one: the
back-off happens deep inside the provider call, several frames below the agent
loop, so the interface showed a step that simply sat there for half a minute
and then carried on as if nothing had happened. Users read that as the app
having hung.

The retry layers publish a :class:`RetryNotice` through the context variable
here; the agent loop installs an observer for the duration of each awaited
call and turns every notice into a ``rate_limit`` SSE event, so the wait is
narrated while it is happening ("Rate limited. Retrying in 12s (1 of 6)")
rather than inferred afterwards from a suspicious gap in the timestamps.

Using a ``contextvars`` observer - the same mechanism as
``app/agent/run_context.py`` - is what lets a notice raised inside a tool's own
LLM call reach the loop without every provider call site growing a parameter
for it, and keeps concurrent runs from seeing each other's waits.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Callable

from app.observability.logging import get_logger

logger = get_logger(__name__)

# Why a call is being retried. Rate limits are the routine, self-correcting
# case worth narrating precisely; everything else is a provider hiccup.
REASON_RATE_LIMIT = "rate_limit"
REASON_TRANSIENT = "transient"


@dataclass(frozen=True)
class RetryNotice:
    """One "waiting before trying again" moment, as the user should see it."""

    reason: str
    attempt: int
    max_attempts: int
    delay_seconds: float
    provider: str = ""
    model: str = ""
    detail: str = ""

    def message(self) -> str:
        """The single line the interface shows for this wait."""
        wait = (
            f"{self.delay_seconds:.0f}s"
            if self.delay_seconds >= 1
            else f"{self.delay_seconds:.1f}s"
        )
        who = f" by {self.provider}" if self.provider else ""
        if self.reason == REASON_RATE_LIMIT:
            return (
                f"Rate limited{who}. Retrying in {wait} "
                f"({self.attempt} of {self.max_attempts})."
            )
        return (
            f"The model provider returned a temporary error. Retrying in {wait} "
            f"({self.attempt} of {self.max_attempts})."
        )


Observer = Callable[[RetryNotice], None]

_observer: ContextVar[Observer | None] = ContextVar(
    "thinkslides_retry_observer", default=None
)


def set_retry_observer(observer: Observer | None) -> Token:
    """Install the observer that receives this run's retry notices."""
    return _observer.set(observer)


def reset_retry_observer(token: Token) -> None:
    """Restore whatever observer was in place before ``set_retry_observer``."""
    _observer.reset(token)


def publish_retry_notice(notice: RetryNotice) -> None:
    """Hand a notice to the current observer, if any.

    Never raises: this is narration, and a failure to narrate must not be able
    to take down the generation it is describing.
    """
    observer = _observer.get()
    if observer is None:
        return
    try:
        observer(notice)
    except Exception as e:  # noqa: BLE001 - see docstring
        logger.warning("retry_notice_observer_failed", error=str(e))
