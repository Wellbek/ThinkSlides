"""
Per-run context for the agent loop.

The registry invokes tools with only the arguments the LLM produced, so there
is no parameter through which the loop can hand a tool the organisation's
resolved configuration. Context variables carry it instead: the loop sets them
for the duration of one run, and any tool executing inside that run reads them.

Two things travel this way:

- the **search configuration**, read by the web_search tool;
- the **chat provider**, read by every tool that makes its own LLM call.
  Without it those tools would quietly fall back to the process-wide provider
  built from environment variables, so choosing Claude in Settings would change
  the agent's reasoning but not the model that actually writes the slides.

Using ``contextvars`` rather than module globals keeps concurrent runs -
several organisations generating at once in one worker - from seeing each
other's configuration.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

from app.ai_settings import SearchConfig
from app.llm.provider import LLMProvider

_search_config: ContextVar[SearchConfig | None] = ContextVar(
    "thinkslides_search_config", default=None
)

_chat_provider: ContextVar[LLMProvider | None] = ContextVar(
    "thinkslides_chat_provider", default=None
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


def set_chat_provider(provider: LLMProvider | None) -> Token:
    """Install the organisation's chat provider for the current run."""
    return _chat_provider.set(provider)


def get_chat_provider() -> LLMProvider | None:
    """The current run's chat provider, or None outside a configured run."""
    return _chat_provider.get()


def reset_chat_provider(token: Token) -> None:
    """Restore whatever provider was in place before ``set_chat_provider``."""
    _chat_provider.reset(token)
