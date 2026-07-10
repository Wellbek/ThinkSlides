"""
Tool: web_search

Lets the agent look up real-world information on the web instead of relying
solely on its parametric knowledge. Each result carries a `url`, so the
agent loop's source extractor surfaces them as clickable sources in the chat.

Supports two pluggable backends, selected via ``WEB_SEARCH_PROVIDER``:

- ``zhipu`` (default): Zhipu's web search API. China-focused - English
  factual queries tend to return mostly Chinese sources (Baidu, Toutiao,
  docin). Kept as the default so existing deployments keep working.
- ``tavily``: Tavily's search API, a Western backend that returns
  English/international sources, giving much better factual grounding for
  non-Chinese content.

Both backends normalize results to ``{title, url, snippet, source}`` so the
rest of the agent loop does not need to know which one was used.

Network calls run in a thread executor so they never block the event loop.
On any failure the tool returns a clear error result (success=False) so the
ReAct loop can continue without crashing - the LLM simply learns that search
is unavailable and proceeds with its own knowledge.
"""

from __future__ import annotations
from app.agent.registry import ToolRegistry, ToolResult, ToolParameter
from app.config import settings
from app.observability.logging import get_logger
import asyncio
import time
import jwt
import httpx

logger = get_logger(__name__)

# How many results to request from the search engine.
DEFAULT_COUNT = 8


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
        try:
            if not query or not query.strip():
                return ToolResult(
                    success=False,
                    error="No search query provided",
                    duration_ms=int((time.time() - start) * 1000),
                )

            provider = settings.web_search_provider
            if not cls._provider_configured(provider):
                return ToolResult(
                    success=False,
                    error=(
                        f"Web search is not configured for provider '{provider}'. "
                        "Set WEB_SEARCH_PROVIDER and the corresponding API key "
                        "(ZHIPU_API_KEY for zhipu, TAVILY_API_KEY for tavily). "
                        "Proceed using your existing knowledge."
                    ),
                    duration_ms=int((time.time() - start) * 1000),
                )

            if provider == "tavily":
                results = await cls._tavily_search(query.strip())
            else:
                results = await cls._zhipu_search(query.strip())

            duration_ms = int((time.time() - start) * 1000)
            logger.info(
                "web_search_completed",
                query=query,
                provider=provider,
                result_count=len(results),
                duration_ms=duration_ms,
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
                    duration_ms=duration_ms,
                )

            return ToolResult(
                success=True,
                data={
                    "query": query,
                    "results": results,
                    "result_count": len(results),
                },
                duration_ms=duration_ms,
            )
        except Exception as e:
            logger.error("web_search_failed", query=query, error=str(e))
            return ToolResult(
                success=False,
                error=f"Web search failed: {str(e)}. Proceed using your existing knowledge.",
                duration_ms=int((time.time() - start) * 1000),
            )

    @staticmethod
    def _provider_configured(provider: str) -> bool:
        """Return whether the chosen backend has the credentials it needs."""
        if provider == "tavily":
            return bool(settings.tavily_api_key)
        # Zhipu backend: needs an API key in the 'id.secret' format used to sign
        # the bearer JWT for the search endpoint.
        return settings.llm_provider == "zhipu" and bool(settings.zhipu_api_key) and "." in settings.zhipu_api_key

    # ── Zhipu backend ────────────────────────────────────────────────────

    @classmethod
    async def _zhipu_search(cls, query: str) -> list[dict]:
        """Call the Zhipu web search REST API directly and normalize results.

        zhipuai SDK 2.0.1 does not wrap the web search endpoint, so we call
        ``/api/paas/v4/web_search`` directly over HTTPS with a self-signed JWT
        (the same auth scheme the SDK uses internally). This keeps the tool
        independent of which SDK version is installed.
        """
        token = cls._build_zhipu_jwt(settings.zhipu_api_key)
        engine = settings.zhipu_web_search_engine or "search_std"
        body = {
            "search_engine": engine,
            "search_query": query,
            "count": DEFAULT_COUNT,
            # search_intent MUST be False: when True, Zhipu first classifies the
            # query intent and returns an EMPTY search_result for any query it
            # labels "SEARCH_NONE" (very common for factual/definitional
            # queries), which would yield zero sources.
            "search_intent": False,
            # Richer snippets so the agent has more content to ground slides on.
            "content_size": settings.zhipu_web_search_content_size,
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        url = "https://open.bigmodel.cn/api/paas/v4/web_search"

        data = await cls._post_json(url, headers, body)
        return cls._parse_zhipu_response(data)

    @staticmethod
    def _build_zhipu_jwt(api_key: str) -> str:
        """Build a Zhipu API bearer token from an ``id.secret`` API key."""
        if "." not in api_key:
            raise ValueError(
                "ZHIPU_API_KEY must be in the 'id.secret' format to use web search."
            )
        key_id, secret = api_key.split(".", 1)
        now_ms = int(round(time.time() * 1000))
        payload = {
            "api_key": key_id,
            "exp": now_ms + 3600 * 1000,
            "timestamp": now_ms,
        }
        return jwt.encode(
            payload,
            secret,
            algorithm="HS256",
            headers={"alg": "HS256", "sign_type": "SIGN"},
        )

    @staticmethod
    def _parse_zhipu_response(response) -> list[dict]:
        """Normalize the Zhipu web search response into a list of result dicts.

        Tolerates: attribute vs dict access; `search_result` being a list or a
        single object; and the various field names the SDK has used.
        """
        def get_field(obj, *keys):
            for k in keys:
                if isinstance(obj, dict):
                    if k in obj and obj[k]:
                        return obj[k]
                else:
                    v = getattr(obj, k, None)
                    if v:
                        return v
            return ""

        # Resolve search_result (attribute or dict key)
        sr = None
        if not isinstance(response, dict):
            sr = getattr(response, "search_result", None)
        if sr is None and isinstance(response, dict):
            sr = response.get("search_result")
        # Some SDK versions nest under `results`
        if sr is None:
            sr = get_field(response, "results")

        if sr is None:
            return []

        items = sr if isinstance(sr, list) else [sr]

        results: list[dict] = []
        seen: set[str] = set()
        for item in items:
            link = get_field(item, "link", "url", "href")
            if not link:
                continue
            title = get_field(item, "title", "media") or link
            snippet = get_field(item, "content", "snippet", "summary")
            media = get_field(item, "media", "source")
            if link in seen:
                continue
            seen.add(link)
            results.append({
                "title": title,
                "url": link,
                "snippet": snippet,
                "source": media,
            })
        return results

    # ── Tavily backend ───────────────────────────────────────────────────

    @classmethod
    async def _tavily_search(cls, query: str) -> list[dict]:
        """Call the Tavily search REST API and normalize results.

        Tavily is a Western search backend purpose-built for LLM grounding,
        so English/international factual queries return far better sources than
        Zhipu's China-focused index. The API key is sent in the request body.
        See https://docs.tavily.com/docs/rest-api/api-reference .
        """
        body = {
            "api_key": settings.tavily_api_key,
            "query": query,
            "max_results": DEFAULT_COUNT,
            "search_depth": settings.tavily_search_depth,
            # We only need result snippets, not a synthesized answer.
            "include_answer": False,
            # Snippets as plain text, not raw HTML.
            "include_raw_content": False,
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        url = "https://api.tavily.com/search"

        data = await cls._post_json(url, headers, body)
        return cls._parse_tavily_response(data)

    @staticmethod
    def _parse_tavily_response(response) -> list[dict]:
        """Normalize the Tavily response into a list of result dicts.

        Tavily returns ``{"results": [{"title", "url", "content", "score"}]}``.
        ``content`` maps to ``snippet``; ``source`` is derived from the URL's
        hostname so the chat can attribute the citation.
        """
        if not isinstance(response, dict):
            return []
        items = response.get("results") or []
        if not isinstance(items, list):
            return []

        results: list[dict] = []
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            link = item.get("url") or ""
            if not link:
                continue
            if link in seen:
                continue
            seen.add(link)
            title = item.get("title") or link
            snippet = item.get("content") or ""
            source = WebSearchTool._host_from_url(link)
            results.append({
                "title": title,
                "url": link,
                "snippet": snippet,
                "source": source,
            })
        return results

    @staticmethod
    def _host_from_url(url: str) -> str:
        """Extract a readable hostname for source attribution.

        Tavily does not return a separate source field, so we derive one from
        the URL. Returns an empty string if the URL has no netloc.
        """
        try:
            from urllib.parse import urlparse
            host = urlparse(url).hostname or ""
            # Strip a leading 'www.' for cleaner attribution.
            return host[4:] if host.startswith("www.") else host
        except Exception:
            return ""

    # ── Shared HTTP helper ───────────────────────────────────────────────

    @staticmethod
    async def _post_json(url: str, headers: dict, body: dict) -> dict:
        """POST JSON in a thread executor and return the parsed JSON body.

        Using a synchronous httpx.Client inside run_in_executor keeps every
        backend's network call off the event loop uniformly.
        """
        loop = asyncio.get_running_loop()

        def _do_request() -> dict:
            with httpx.Client(timeout=30.0) as http_client:
                resp = http_client.post(url, headers=headers, json=body)
                resp.raise_for_status()
                return resp.json()

        return await loop.run_in_executor(None, _do_request)
