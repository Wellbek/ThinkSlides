"""
Per-run context for the agent loop.

The registry invokes tools with only the arguments the LLM produced, so there
is no parameter through which the loop can hand a tool the organisation's
resolved configuration. Context variables carry it instead: the loop sets them
for the duration of one run, and any tool executing inside that run reads them.

Two things travel this way:

- the **search configuration**, read by the web_search tool;
- the **citation settings** - whether the user asked for source references in
  the document, and what the run has researched - read by
  finalize_and_render_pptx when it puts the trail into the .pptx itself;
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

# Whether the user asked for source references in the rendered document, and
# the findings to draw them from. Both default to "no citations", so a tool
# running outside a configured run renders exactly what it always did.
_include_sources: ContextVar[bool] = ContextVar(
    "thinkslides_include_sources", default=False
)

_research_findings: ContextVar[list | None] = ContextVar(
    "thinkslides_research_findings", default=None
)


def set_include_sources(enabled: bool) -> Token:
    """Install whether this run cites its sources in the document."""
    return _include_sources.set(bool(enabled))


def get_include_sources() -> bool:
    """Whether the current run cites its sources; False outside a run."""
    return _include_sources.get()


def reset_include_sources(token: Token) -> None:
    _include_sources.reset(token)


def set_research_findings(findings: list | None) -> Token:
    """Install the findings this run has gathered, for the reference list."""
    return _research_findings.set(findings)


def get_research_findings() -> list | None:
    """The current run's research findings, or None outside a run."""
    return _research_findings.get()


def reset_research_findings(token: Token) -> None:
    _research_findings.reset(token)


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
