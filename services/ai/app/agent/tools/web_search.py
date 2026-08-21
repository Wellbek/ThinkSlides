"""
Tool: web_search

Lets the agent look up real-world information on the web instead of relying
solely on its parametric knowledge. Each result carries a `url`, so the agent
loop's source extractor surfaces them as clickable sources in the chat.

The backend is not chosen here. Deep Thinking mode resolves the organisation's
configured backend (the model provider's native search, Tavily, or Firecrawl)
and installs it on the run context; this tool just uses whatever is there. See
``app/agent/tools/search_backends.py`` for the backends themselves and
``app/ai_settings.py`` for how one is selected.

When a run has no configured backend - Simple mode, or an organisation that has
not set one up - the tool returns a clear error result (success=False) rather
than raising, so the ReAct loop continues and the LLM simply learns that search
is unavailable and proceeds with its own knowledge.
"""

from __future__ import annotations

import datetime
import re
import time

from app import ai_settings
from app.agent.registry import ToolParameter, ToolRegistry, ToolResult
from app.agent.run_context import get_search_config
from app.agent.tools.search_backends import normalize_recency, recency_supported, run_search
from app.observability.logging import get_logger

logger = get_logger(__name__)


# Words that mean "give me the newest thing", in the phrasings a model
# actually produces.
_RECENCY_WORDS = (
    "latest", "most recent", "newest", "current", "currently", "up to date",
    "up-to-date", "this year", "recent", "so far", "as of today", "today",
)

_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")


def strip_stale_year(query: str, today: datetime.date) -> tuple[str, str]:
    """Drop a past year from a query that also asks for the latest.

    Returns ``(query, removed_year)``, with ``removed_year`` empty when there
    was nothing to correct.

    A model reaches for the year its training data ended in whenever it wants
    to say "now", and the system prompt stating today's date does not reliably
    stop it - "latest Green Climate Fund approved projects 2023", asked in
    2026, is a real query this tool received. Left alone it defeats the whole
    request: the search engine honours the year and returns exactly the stale
    pages the word "latest" was meant to exclude.

    The signature is the contradiction, not the year: asking for the newest
    thing while pinning a year that has already passed cannot be what was
    meant. A past year *without* a recency word is left alone, because
    "GCF approvals in 2019" is a perfectly good question.
    """
    if not any(word in query.lower() for word in _RECENCY_WORDS):
        return query, ""

    stale = [m.group(0) for m in _YEAR_RE.finditer(query) if int(m.group(0)) < today.year]
    if not stale:
        return query, ""

    cleaned = _YEAR_RE.sub(
        lambda m: "" if int(m.group(0)) < today.year else m.group(0), query
    )
    # Tidy the hole the year left behind: doubled spaces, and a trailing
    # connective that now dangles ("projects in " -> "projects").
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    cleaned = re.sub(r"[\s,;:-]+$", "", cleaned)
    cleaned = re.sub(r"\b(in|for|from|during|of)$", "", cleaned).strip()

    # Never hand back a worse query than we were given. Once the year is out,
    # "latest 2023" is just "latest" - no subject left to search for - and a
    # badly dated query still beats a contentless one.
    if not _has_subject(cleaned):
        return query, stale[0]
    return cleaned, stale[0]


def _has_subject(query: str) -> bool:
    """Whether anything is left to search for once the framing is discounted."""
    remainder = query.lower()
    for word in _RECENCY_WORDS:
        remainder = remainder.replace(word, " ")
    return bool(re.sub(r"[^a-z0-9]+", "", remainder))


class WebSearchTool:
    """Search the web and return results with URLs for source attribution."""

    @classmethod
    def register(cls, registry: ToolRegistry) -> None:
        registry.register_tool(
            name="web_search",
            description=(
                "Search the web for real-world facts, statistics, definitions, or "
                "current information you may not know precisely. Returns a list of "
                "results each with a title, url, and snippet. Use this BEFORE writing "
                "factual content so your slides are accurate and cite real sources. "
                "Do not use it for topics you already know well or for the user's own "
                "uploaded documents."
            ),
            fn=cls.run,
            parameters=[
                ToolParameter(
                    name="query",
                    type="string",
                    description="The search query - a concise, specific phrase.",
                ),
                ToolParameter(
                    name="recency",
                    type="string",
                    required=False,
                    enum=["day", "week", "month", "year"],
                    description=(
                        "Optional. Bias results toward freshly published pages within "
                        "this window. Use when the request asks for the 'most recent' "
                        "or 'latest' information - omit for evergreen topics, where a "
                        "recency filter can starve the search of good results."
                    ),
                ),
            ],
        )

    @classmethod
    async def run(cls, query: str, recency: str | None = None) -> ToolResult:
        start = time.time()

        def elapsed() -> int:
            return int((time.time() - start) * 1000)

        try:
            if not query or not query.strip():
                return ToolResult(
                    success=False, error="No search query provided", duration_ms=elapsed()
                )

            # Corrected before anything else looks at it, so the stale year
            # reaches neither the backend nor the logged query.
            query, stale_year = strip_stale_year(query, datetime.date.today())
            if stale_year:
                logger.info(
                    "web_search_stale_year_stripped", query=query, year=stale_year
                )
                # The request was clearly about recency; say so the way the
                # backends can act on, now that the query no longer does.
                recency = recency or "year"

            config = get_search_config()
            if config is None:
                # Outside a Deep Thinking run, fall back to whatever the
                # environment configures so direct/legacy invocations still work.
                config, reason = ai_settings.resolve_search_config(None)
            else:
                reason = ""

            if config is None:
                return ToolResult(
                    success=False,
                    error=(
                        f"Web search is not available. {reason} "
                        "Configure a deep-search backend in Settings, or proceed "
                        "using your existing knowledge."
                    ).strip(),
                    duration_ms=elapsed(),
                )

            # Validated once here, at the tool boundary, so an off-schema value
            # from the model never reaches an upstream request body (run_search
            # normalizes defensively too, but the tool result below needs the
            # sanitized value to report accurately on what actually happened).
            requested_recency = recency
            recency = normalize_recency(recency)
            if requested_recency and not recency:
                logger.info(
                    "web_search_recency_invalid", query=query, requested=requested_recency
                )

            results = await run_search(config, query.strip(), recency=recency)

            logger.info(
                "web_search_completed",
                query=query,
                backend=config.backend,
                recency=recency,
                result_count=len(results),
                duration_ms=elapsed(),
            )

            notes = []
            if stale_year:
                notes.append(
                    f"Your query named {stale_year}, which is in the past, while asking "
                    f"for current information. It was searched as '{query}' with "
                    f"recency='{recency}' instead. Today is {datetime.date.today():%Y-%m-%d} "
                    "- do not date a query from memory."
                )
            if recency and not recency_supported(config):
                notes.append(
                    f"Requested recency='{recency}', but the configured '{config.backend}' "
                    "search backend does not support filtering by recency - these results "
                    "are unfiltered by date. Check each result's published date yourself."
                )
                logger.info("web_search_recency_unsupported", backend=config.backend, recency=recency)
            recency_note = " ".join(notes)

            if not results:
                return ToolResult(
                    success=True,
                    data={
                        "query": query,
                        "recency": recency,
                        "results": [],
                        "result_count": 0,
                        "note": recency_note
                        or "No search results returned. Proceed using your existing knowledge.",
                    },
                    duration_ms=elapsed(),
                )

            data = {"query": query, "recency": recency, "results": results, "result_count": len(results)}
            if recency_note:
                data["note"] = recency_note
            return ToolResult(success=True, data=data, duration_ms=elapsed())
        except Exception as e:
            logger.error("web_search_failed", query=query, error=str(e))
            return ToolResult(
                success=False,
                error=f"Web search failed: {str(e)}. Proceed using your existing knowledge.",
                duration_ms=elapsed(),
            )
