"""
Pluggable web-search backends for Deep Thinking mode.

Every backend normalizes to ``{title, url, snippet, source, published}`` so the
agent loop, the source extractor, and the chat UI never learn which one ran.
``published`` is best-effort - most backends only carry a date for news-shaped
results - and empty when the backend did not say. A reference that reaches the
rendered deck without one simply does not claim a date.

Two families:

- **Native** - the configured model provider's own search API, using the
  credentials already set up for that provider. No extra subscription, but the
  index is whatever the provider ships (Zhipu's is China-focused, which is the
  root cause of issue #25).
- **External** - a dedicated search API (Tavily, Firecrawl) with its own key.

Network calls run in a thread executor or through the provider's async SDK, so
they never block the event loop. Failures raise; the calling tool turns them
into a graceful ``ToolResult`` so the ReAct loop can continue without search.
"""

from __future__ import annotations

import asyncio
import re
import time
from urllib.parse import urlparse

import httpx
import jwt

from app.ai_settings import SearchConfig
from app.config import settings
from app.agent.tools._today import today_line
from app.llm import usage
from app.observability import trace
from app.observability.logging import get_logger

logger = get_logger(__name__)

# How many results to request from each backend. Backends routinely return
# fewer than asked for (and the loop then dedupes across searches), so this
# sits above the number of sources a slide actually wants to cite.
DEFAULT_COUNT = 10

ZHIPU_SEARCH_URL = "https://open.bigmodel.cn/api/paas/v4/web_search"
TAVILY_SEARCH_URL = "https://api.tavily.com/search"
FIRECRAWL_BASE_URL = "https://api.firecrawl.dev"

# Anthropic's server-side search tool. The dated variants are model-gated: the
# newer one (dynamic filtering) needs a recent model, so we fall back once.
ANTHROPIC_SEARCH_TOOL = "web_search_20260209"
ANTHROPIC_SEARCH_TOOL_FALLBACK = "web_search_20250305"

# The only recency values any backend understands. Validated here - not deep
# inside each backend - so an off-schema value from the model (or from a
# resumed/replayed tool call) never reaches an upstream API, and every caller
# that wants to know "is this a real recency value" (the already-run cache
# check in loop.py included) normalizes the exact same way a real call would.
VALID_RECENCY = ("day", "week", "month", "year")


def normalize_recency(value) -> str:
    """``value`` if it's a known recency filter, else "" (no filter)."""
    value = str(value or "").strip().lower()
    return value if value in VALID_RECENCY else ""


def recency_supported(config: SearchConfig) -> bool:
    """Whether ``run_search``'s ``recency`` argument affects this config's backend.

    Only Zhipu (native, non-Anthropic/OpenAI) and Tavily currently implement
    a recency filter. Callers use this to warn rather than silently drop the
    argument when it was requested against a backend that ignores it.
    """
    if config.backend == "tavily":
        return True
    if config.backend == "native":
        provider = config.model.provider if config.model else None
        return provider not in ("anthropic", "openai")
    return False


async def run_search(
    config: SearchConfig, query: str, count: int = DEFAULT_COUNT, recency: str | None = None
) -> list[dict]:
    """Execute a search through the configured backend.

    ``recency`` asks the backend to bias or filter toward freshly published
    results - "day"/"week"/"month"/"year" (Zhipu's own vocabulary; Tavily's
    ``time_range`` is mapped onto the same four values). Only Zhipu and Tavily
    support it (see ``recency_supported``); other backends silently ignore it
    rather than erroring, since asking for recency is a hint, not a hard
    requirement of the tool contract. Always normalized here so an invalid
    value never reaches an upstream request body.
    """
    start = time.time()
    recency = normalize_recency(recency)

    if config.backend == "native":
        results = await _native_search(config, query, count, recency)
    elif config.backend == "tavily":
        results = await _tavily_search(config, query, count, recency)
    elif config.backend == "firecrawl":
        results = await _firecrawl_search(config, query, count)
    else:
        raise ValueError(f"Unknown search backend '{config.backend}'")

    logger.info(
        "web_search_backend_completed",
        backend=config.backend,
        provider=config.model.provider if config.model else None,
        result_count=len(results),
        duration_ms=int((time.time() - start) * 1000),
    )
    return _dedupe(results)


async def _native_search(
    config: SearchConfig, query: str, count: int, recency: str | None = None
) -> list[dict]:
    if config.model is None:
        raise ValueError("Native search needs a configured model provider")
    if config.model.provider == "anthropic":
        return await _anthropic_search(config, query, count)
    if config.model.provider == "openai":
        return await _openai_search(config, query, count)
    return await _zhipu_search(config, query, count, recency)


def _dedupe(results: list[dict]) -> list[dict]:
    seen: set[str] = set()
    deduped: list[dict] = []
    for item in results:
        url = item.get("url")
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append(item)
    return deduped


def host_from_url(url: str) -> str:
    """Readable hostname for source attribution, with a leading 'www.' stripped."""
    try:
        host = urlparse(url).hostname or ""
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


# Backends date a page in whatever their upstream said: an ISO timestamp, a
# bare date, or (Anthropic) a relative "3 days ago". Only the first two carry
# information a reader can act on, so the rest is dropped rather than printed
# on a slide that will be read months later.
_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


def published_date(value) -> str:
    """A page's publication date as ``YYYY-MM-DD``, or "" when not usable."""
    if not value or not isinstance(value, str):
        return ""
    match = _ISO_DATE_RE.match(value.strip())
    if not match:
        return ""
    year, month, day = (int(part) for part in match.groups())
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return ""
    return f"{year:04d}-{month:02d}-{day:02d}"


async def post_json(url: str, headers: dict, body: dict, timeout: float = 30.0) -> dict:
    """POST JSON off the event loop and return the parsed body.

    A synchronous httpx.Client inside run_in_executor keeps every HTTP backend's
    network call off the loop uniformly, and gives tests a single patch point.
    """
    loop = asyncio.get_running_loop()

    def _do_request() -> dict:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, headers=headers, json=body)
            response.raise_for_status()
            return response.json()

    return await loop.run_in_executor(None, _do_request)


# ── Zhipu (native) ─────────────────────────────────────────────────────


def build_zhipu_jwt(api_key: str) -> str:
    """Build a Zhipu bearer token from an ``id.secret`` API key.

    The zhipuai SDK does not wrap the web search endpoint, so we call it
    directly and sign the token the same way the SDK does internally.
    """
    if "." not in api_key:
        raise ValueError("Zhipu API key must be in 'id.secret' form to use web search.")
    key_id, secret = api_key.split(".", 1)
    now_ms = int(round(time.time() * 1000))
    return jwt.encode(
        {"api_key": key_id, "exp": now_ms + 3600 * 1000, "timestamp": now_ms},
        secret,
        algorithm="HS256",
        headers={"alg": "HS256", "sign_type": "SIGN"},
    )


async def _zhipu_search(
    config: SearchConfig, query: str, count: int, recency: str | None = None
) -> list[dict]:
    api_key = config.model.api_key if config.model else settings.zhipu_api_key
    body = {
        "search_engine": settings.zhipu_web_search_engine or "search_std",
        "search_query": query,
        "count": count,
        # search_intent MUST stay False: when True, Zhipu first classifies the
        # query and returns an EMPTY search_result for anything it labels
        # "SEARCH_NONE" - very common for factual queries.
        "search_intent": False,
        "content_size": settings.zhipu_web_search_content_size,
    }
    if recency:
        # Zhipu's own vocabulary: day/week/month/year/noLimit.
        body["search_recency_filter"] = recency
    headers = {
        "Authorization": f"Bearer {build_zhipu_jwt(api_key)}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    return parse_zhipu_response(await post_json(ZHIPU_SEARCH_URL, headers, body))


def parse_zhipu_response(response) -> list[dict]:
    """Normalize Zhipu's response.

    Tolerates attribute vs dict access, ``search_result`` being a list or a
    single object, and the various field names the SDK has used over time.
    """
    def get_field(obj, *keys):
        for key in keys:
            if isinstance(obj, dict):
                if obj.get(key):
                    return obj[key]
            else:
                value = getattr(obj, key, None)
                if value:
                    return value
        return ""

    search_result = None
    if not isinstance(response, dict):
        search_result = getattr(response, "search_result", None)
    if search_result is None and isinstance(response, dict):
        search_result = response.get("search_result")
    if search_result is None:
        search_result = get_field(response, "results")
    if search_result is None:
        return []

    items = search_result if isinstance(search_result, list) else [search_result]

    results: list[dict] = []
    for item in items:
        link = get_field(item, "link", "url", "href")
        if not link:
            continue
        results.append({
            "title": get_field(item, "title", "media") or link,
            "url": link,
            "snippet": get_field(item, "content", "snippet", "summary"),
            "source": get_field(item, "media", "source") or host_from_url(link),
            "published": published_date(
                get_field(item, "publish_date", "publishDate", "published_date", "date")
            ),
        })
    return results


# ── Tavily (external) ──────────────────────────────────────────────────


async def _tavily_search(
    config: SearchConfig, query: str, count: int, recency: str | None = None
) -> list[dict]:
    body = {
        "api_key": config.api_key,
        "query": query,
        "max_results": count,
        "search_depth": settings.tavily_search_depth,
        # We want result snippets, not a synthesized answer or raw HTML.
        "include_answer": False,
        "include_raw_content": False,
    }
    if recency:
        # Tavily's time_range takes the same day/week/month/year vocabulary.
        body["time_range"] = recency
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    url = f"{(config.base_url or '').rstrip('/')}/search" if config.base_url else TAVILY_SEARCH_URL
    return parse_tavily_response(await post_json(url, headers, body))


def parse_tavily_response(response) -> list[dict]:
    """Normalize Tavily's ``{"results": [{title, url, content, score}]}``."""
    if not isinstance(response, dict):
        return []
    items = response.get("results")
    if not isinstance(items, list):
        return []

    results: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        link = item.get("url") or ""
        if not link:
            continue
        results.append({
            "title": item.get("title") or link,
            "url": link,
            "snippet": item.get("content") or "",
            # Tavily returns no source field, so derive one from the URL.
            "source": host_from_url(link),
            # Only populated for news-shaped results.
            "published": published_date(item.get("published_date")),
        })
    return results


# ── Firecrawl (external) ───────────────────────────────────────────────


async def _firecrawl_search(config: SearchConfig, query: str, count: int) -> list[dict]:
    base = (config.base_url or FIRECRAWL_BASE_URL).rstrip("/")
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    body = {
        "query": query,
        "limit": count,
        "sources": ["web"],
        # Scraped markdown gives far richer grounding than a search snippet;
        # onlyMainContent drops nav/boilerplate so the prompt stays useful.
        "scrapeOptions": {"formats": ["markdown"], "onlyMainContent": True},
    }
    # Firecrawl's scraping path is slower than a plain search API.
    return parse_firecrawl_response(
        await post_json(f"{base}/v2/search", headers, body, timeout=90.0)
    )


# Scraped pages can be enormous; cap what reaches the prompt.
FIRECRAWL_SNIPPET_CHARS = 1500

# How many candidate image URLs to keep per scraped page for the
# select_source_image catalog - a page can embed dozens of icons/avatars/
# tracking pixels, only the first few are worth surfacing as candidates.
MAX_IMAGES_PER_RESULT = 4
_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\((https?://[^\s)]+\.(?:png|jpe?g|gif|webp))\)", re.IGNORECASE)


def _extract_image_urls_from_markdown(markdown: str) -> list[str]:
    """Best-effort candidate image URLs scraped out of a page's markdown.

    These are only ever candidates - select_source_image still has to judge
    each one confidently relevant to a specific slide before it's used, and
    defaults to using none.
    """
    if not markdown:
        return []
    seen: list[str] = []
    for url in _MARKDOWN_IMAGE_RE.findall(markdown):
        if url not in seen:
            seen.append(url)
        if len(seen) >= MAX_IMAGES_PER_RESULT:
            break
    return seen


def parse_firecrawl_response(response) -> list[dict]:
    """Normalize Firecrawl's search response.

    v2 returns ``{"data": {"web": [...]}}``; older shapes return ``{"data": [...]}``.
    Each item carries ``url``, ``title``, ``description`` and - when scraping is
    enabled - ``markdown``, which is preferred as the snippet. ``images`` is
    populated from image URLs found in that same markdown, when any exist -
    the only backend where a candidate image is naturally tied to a specific
    result, since scraping is already enabled.
    """
    if not isinstance(response, dict):
        return []
    data = response.get("data")
    if isinstance(data, dict):
        items = data.get("web") or []
    elif isinstance(data, list):
        items = data
    else:
        items = []
    if not isinstance(items, list):
        return []

    results: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        link = item.get("url") or ""
        if not link:
            continue
        markdown = item.get("markdown") or ""
        snippet = (markdown or item.get("description") or "").strip()
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        results.append({
            "title": item.get("title") or link,
            "url": link,
            "snippet": snippet[:FIRECRAWL_SNIPPET_CHARS],
            "source": host_from_url(link),
            "images": _extract_image_urls_from_markdown(markdown),
            # Scraped from the page's own <meta> tags when Firecrawl found any.
            "published": published_date(
                metadata.get("publishedTime")
                or metadata.get("article:published_time")
                or item.get("publishedTime")
            ),
        })
    return results


# ── Anthropic (native) ─────────────────────────────────────────────────


async def _anthropic_search(config: SearchConfig, query: str, count: int) -> list[dict]:
    """Search through Claude's server-side web search tool.

    The raw ``web_search_result`` blocks carry encrypted page content, so the
    readable material comes from the citations Claude attaches to its answer:
    each one has a title, a URL, and the quoted text it drew on - exactly the
    normalized shape the rest of the pipeline wants.
    """
    from anthropic import AsyncAnthropic

    credentials = config.model
    client_kwargs = {"api_key": credentials.api_key}
    if credentials.base_url:
        client_kwargs["base_url"] = credentials.base_url
    client = AsyncAnthropic(**client_kwargs)

    request = {
        "model": credentials.model,
        "max_tokens": 4096,
        "messages": [{
            "role": "user",
            "content": (
                f"{today_line()}\n\n"
                f"Search the web for: {query}\n\n"
                "Report the key facts you find. Cite every claim - the citations "
                "are the actual output being collected."
            ),
        }],
    }

    for tool_type in (ANTHROPIC_SEARCH_TOOL, ANTHROPIC_SEARCH_TOOL_FALLBACK):
        try:
            with trace.span("llm", "native_search", provider="anthropic",
                            model=credentials.model):
                response = await client.messages.create(
                    **request,
                    tools=[{"type": tool_type, "name": "web_search", "max_uses": max(1, count // 4)}],
                )
                usage.record("anthropic", credentials.model, response)
            return parse_anthropic_citations(response)
        except Exception as exc:  # noqa: BLE001 - retry only the model-gating case
            if tool_type is ANTHROPIC_SEARCH_TOOL and "web_search" in str(exc):
                logger.info(
                    "anthropic_search_tool_downgraded",
                    model=credentials.model,
                    error=str(exc),
                )
                continue
            raise
    return []


def parse_anthropic_citations(response) -> list[dict]:
    """Collect ``web_search_result_location`` citations off the answer's text blocks."""
    results: list[dict] = []
    for block in getattr(response, "content", None) or []:
        block_type = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
        if block_type != "text":
            continue
        citations = (
            block.get("citations") if isinstance(block, dict)
            else getattr(block, "citations", None)
        ) or []
        for citation in citations:
            if isinstance(citation, dict):
                url = citation.get("url")
                title = citation.get("title")
                cited_text = citation.get("cited_text")
            else:
                url = getattr(citation, "url", None)
                title = getattr(citation, "title", None)
                cited_text = getattr(citation, "cited_text", None)
            if not url:
                continue
            results.append({
                "title": title or url,
                "url": url,
                "snippet": (cited_text or "").strip(),
                "source": host_from_url(url),
                # Anthropic dates a result on the tool-result block ("3 days
                # ago"), not on the citation, and a relative age is not
                # something to print on a slide - so this is normally empty.
                "published": published_date(
                    citation.get("page_age") if isinstance(citation, dict)
                    else getattr(citation, "page_age", None)
                ),
            })
    return results


# ── OpenAI (native) ────────────────────────────────────────────────────

# Characters of surrounding answer text used as a snippet for each citation.
OPENAI_SNIPPET_CHARS = 400


SEARCH_INSTRUCTION = "Report the key facts, citing sources."


def _search_prompt(query: str) -> str:
    """What both OpenAI search routes ask for.

    Led by the date because a search model reading "latest" has the same
    training-cutoff problem as the model that wrote the query - see
    app/agent/tools/_today.py.
    """
    return f"{today_line()}\n\nSearch the web for: {query}\n\n{SEARCH_INSTRUCTION}"

# The chat-completions route to OpenAI search only ever worked on purpose-built
# search models, and those are named, finite, and retired on a schedule: the
# gpt-4o `-search-preview` pair shut down on 23 July 2026. Everything current
# searches through the Responses API instead, which is model-agnostic. These
# names exist only to keep a legacy or proxied deployment working - a model not
# listed here takes the Responses route, so a new model id needs no change here.
OPENAI_CHAT_COMPLETIONS_SEARCH_MODELS = (
    "gpt-4o-search-preview",
    "gpt-4o-mini-search-preview",
    "gpt-5-search-api",
)

# What a request to /responses looks like when the endpoint has never heard of
# it - a third-party service that emulates chat completions and nothing else.
_NO_ENDPOINT_MARKERS = ("404", "not found", "unknown path", "unrecognized request")
# ... and what it looks like when the endpoint is fine and the model is not.
# Checked first, because that 404 must surface as itself rather than sending a
# second doomed request down the legacy route.
_MODEL_MISSING_MARKERS = (
    "does not exist", "no such model", "model not found", "unknown model",
)


def _endpoint_lacks_responses_api(exc: Exception) -> bool:
    """Whether this failure means "no such endpoint", not "bad request"."""
    if isinstance(exc, AttributeError):
        return True  # an openai SDK predating the Responses API
    text = str(exc).lower()
    if any(marker in text for marker in _MODEL_MISSING_MARKERS):
        return False
    return any(marker in text for marker in _NO_ENDPOINT_MARKERS)


def _uses_chat_completions_search(model: str) -> bool:
    """Whether this model searches the old way, through web_search_options."""
    return any(name in model for name in OPENAI_CHAT_COMPLETIONS_SEARCH_MODELS)


async def _openai_search(config: SearchConfig, query: str, count: int) -> list[dict]:
    """Search through OpenAI's built-in web search.

    Two routes, because OpenAI moved the feature. The Responses API's
    ``web_search`` tool works with the ordinary reasoning models and is the only
    route with a future; ``web_search_options`` on chat completions works only
    with the retired search-preview models. Current models take the first route,
    named legacy models take the second, and an endpoint that has no /responses
    at all falls back rather than failing.
    """
    from openai import AsyncOpenAI

    credentials = config.model
    client_kwargs = {"api_key": credentials.api_key}
    if credentials.base_url:
        client_kwargs["base_url"] = credentials.base_url
    client = AsyncOpenAI(**client_kwargs)

    if _uses_chat_completions_search(credentials.model):
        return await _openai_chat_completions_search(client, credentials.model, query)

    try:
        return await _openai_responses_search(client, credentials.model, query, count)
    except Exception as exc:  # noqa: BLE001 - only the "no such endpoint" case
        if not _endpoint_lacks_responses_api(exc):
            raise
        logger.info(
            "openai_search_falling_back_to_chat_completions",
            model=credentials.model,
            error=str(exc)[:200],
        )
        return await _openai_chat_completions_search(client, credentials.model, query)


async def _openai_responses_search(
    client, model: str, query: str, count: int
) -> list[dict]:
    """Search with the Responses API's ``web_search`` tool.

    The tool is passed through ``extra_body`` for the same reason
    web_search_options is below - a named argument the installed SDK does not
    know raises TypeError before any request leaves the process, while
    extra_body is forwarded verbatim on every SDK version.
    """
    with trace.span("llm", "native_search", provider="openai", model=model):
        response = await client.responses.create(
            model=model,
            input=_search_prompt(query),
            extra_body={
                "tools": [{"type": "web_search"}],
                "max_tool_calls": max(1, count // 4),
            },
        )
        usage.record("openai", model, response)
    return parse_openai_response_annotations(response)


async def _openai_chat_completions_search(client, model: str, query: str) -> list[dict]:
    """Search with ``web_search_options``, the route the retired models used."""
    with trace.span("llm", "native_search", provider="openai", model=model):
        response = await client.chat.completions.create(
            model=model,
            messages=[{
                "role": "user",
                "content": _search_prompt(query),
            }],
            extra_body={"web_search_options": {}},
        )
        usage.record("openai", model, response)
    return parse_openai_annotations(response)


def parse_openai_response_annotations(response) -> list[dict]:
    """Turn a Responses API answer into normalized results.

    Same ``url_citation`` annotations as chat completions, one level deeper:
    the response carries ``output`` items, message items carry ``content``
    blocks, and the annotations hang off the ``output_text`` blocks.
    """
    results: list[dict] = []
    for item in _attr(response, "output") or []:
        for block in _attr(item, "content") or []:
            text = _attr(block, "text") or ""
            for annotation in _attr(block, "annotations") or []:
                result = _openai_citation(annotation, text)
                if result:
                    results.append(result)
    return results


def _attr(obj, name):
    """Read one field off a dict or a typed SDK model, whichever came back."""
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _openai_citation(annotation, content: str) -> dict | None:
    """One ``url_citation`` annotation, normalized against its answer text.

    OpenAI reports citations as offsets into the answer rather than as quoted
    snippets, so the snippet is the slice of the answer the citation points at.
    In the Responses API the citation's fields sit on the annotation itself; in
    chat completions they sit under a nested ``url_citation``.
    """
    citation = _attr(annotation, "url_citation") or annotation
    url = _attr(citation, "url")
    if not url:
        return None
    start, end = _attr(citation, "start_index"), _attr(citation, "end_index")

    if isinstance(start, int) and isinstance(end, int) and content:
        snippet = content[max(0, start - OPENAI_SNIPPET_CHARS):end].strip()
    else:
        snippet = content[:OPENAI_SNIPPET_CHARS].strip()

    return {
        "title": _attr(citation, "title") or url,
        "url": url,
        "snippet": snippet,
        "source": host_from_url(url),
        # OpenAI's url_citation annotations carry no date.
        "published": "",
    }


def parse_openai_annotations(response) -> list[dict]:
    """Turn a chat-completions answer's ``url_citation`` annotations into results."""
    choices = getattr(response, "choices", None) or []
    if not choices:
        return []
    message = choices[0].message
    content = getattr(message, "content", "") or ""

    results: list[dict] = []
    for annotation in getattr(message, "annotations", None) or []:
        result = _openai_citation(annotation, content)
        if result:
            results.append(result)
    return results
