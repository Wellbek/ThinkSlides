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

import time

from app import ai_settings
from app.agent.registry import ToolParameter, ToolRegistry, ToolResult
from app.agent.run_context import get_search_config
from app.agent.tools.search_backends import run_search
from app.observability.logging import get_logger

logger = get_logger(__name__)


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
            ],
        )

    @classmethod
    async def run(cls, query: str) -> ToolResult:
        start = time.time()

        def elapsed() -> int:
            return int((time.time() - start) * 1000)

        try:
            if not query or not query.strip():
                return ToolResult(
                    success=False, error="No search query provided", duration_ms=elapsed()
                )

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

            results = await run_search(config, query.strip())

            logger.info(
                "web_search_completed",
                query=query,
                backend=config.backend,
                result_count=len(results),
                duration_ms=elapsed(),
            )

            if not results:
                return ToolResult(
                    success=True,
                    data={
                        "query": query,
                        "results": [],
                        "result_count": 0,
                        "note": "No search results returned. Proceed using your existing knowledge.",
                    },
                    duration_ms=elapsed(),
                )

            return ToolResult(
                success=True,
                data={"query": query, "results": results, "result_count": len(results)},
                duration_ms=elapsed(),
            )
        except Exception as e:
            logger.error("web_search_failed", query=query, error=str(e))
            return ToolResult(
                success=False,
                error=f"Web search failed: {str(e)}. Proceed using your existing knowledge.",
                duration_ms=elapsed(),
            )
