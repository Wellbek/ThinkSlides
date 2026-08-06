"""
Per-run context for the agent loop.

The registry invokes tools with only the arguments the LLM produced, so there
is no parameter through which the loop can hand a tool the organisation's
resolved configuration. A context variable carries it instead: the loop sets it
for the duration of one run, and any tool executing inside that run reads it.

Using ``contextvars`` rather than a module global keeps concurrent runs -
several organisations generating at once in one worker - from seeing each
other's configuration.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

from app.ai_settings import SearchConfig

_search_config: ContextVar[SearchConfig | None] = ContextVar(
    "thinkslides_search_config", default=None
)


def set_search_config(config: SearchConfig | None) -> Token:
    """Install the search configuration for the current run."""
    return _search_config.set(config)


def get_search_config() -> SearchConfig | None:
    """The current run's search configuration, or None outside a run."""
    return _search_config.get()


def reset_search_config(token: Token) -> None:
    """Restore whatever configuration was in place before ``set_search_config``."""
    _search_config.reset(token)
