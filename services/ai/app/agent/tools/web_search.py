"""
Tool: web_search

Lets the agent look up real-world information on the web instead of relying
solely on its parametric knowledge. Each result carries a `url`, so the
agent loop's source extractor surfaces them as clickable sources in the chat.

Backed by Zhipu's web search API (the default provider). The SDK method name
(`web_search` vs the older `web_search_pro`) and the response shape vary
across SDK versions, so both the call and the parsing are defensive: it works
with either method name and tolerates a single-object or list `search_result`,
accessing fields by attribute or by dict key.

Runs the synchronous SDK call in a thread executor so it never blocks the
event loop. On any failure it returns a clear error result (success=False) so
the ReAct loop can continue without crashing - the LLM simply learns that
search is unavailable and proceeds with its own knowledge.
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

            if settings.llm_provider != "zhipu" or not settings.zhipu_api_key:
                return ToolResult(
                    success=False,
                    error=(
                        "Web search is only available with the Zhipu provider and a "
                        "configured API key. Proceed using your existing knowledge."
                    ),
                    duration_ms=int((time.time() - start) * 1000),
                )

            results = await cls._zhipu_search(query.strip())

            duration_ms = int((time.time() - start) * 1000)
            logger.info(
                "web_search_completed",
                query=query,
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

        loop = asyncio.get_running_loop()

        def _do_request() -> dict:
            with httpx.Client(timeout=30.0) as http_client:
                resp = http_client.post(url, headers=headers, json=body)
                resp.raise_for_status()
                return resp.json()

        data = await loop.run_in_executor(None, _do_request)
        return cls._parse_response(data)

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
    def _parse_response(response) -> list[dict]:
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
