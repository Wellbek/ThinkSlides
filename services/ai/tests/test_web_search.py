"""
Tests for the web_search agent tool.

Both backends (Zhipu and Tavily) are exercised by mocking the shared HTTP
helper so no real network call is made. The tests assert that results are
normalized to the {title, url, snippet, source} shape the agent loop expects,
and that provider dispatch + the "not configured" guard behave correctly.
"""

import pytest
from unittest.mock import patch, AsyncMock

from app.agent.tools.web_search import WebSearchTool
from app.config import settings


def _set_provider(provider: str, **overrides):
    """Point the tool at a backend (and override related settings) for a test."""
    settings.web_search_provider = provider
    for key, value in overrides.items():
        setattr(settings, key, value)


@pytest.mark.asyncio
async def test_tavily_search_normalizes_results():
    """Tavily results map into {title, url, snippet, source} with a host source."""
    _set_provider("tavily", tavily_api_key="tvly-test")
    tavily_response = {
        "results": [
            {
                "title": "Three Tallest Mountains",
                "url": "https://en.wikipedia.org/wiki/List_of_highest_mountains",
                "content": "Mount Everest is 8,848 m tall.",
                "score": 0.95,
            },
            {
                "title": "Britannica - Everest",
                "url": "https://www.britannica.com/place/Mount-Everest",
                "content": "Everest sits on the Nepal-China border.",
                "score": 0.9,
            },
        ]
    }

    with patch.object(
        WebSearchTool, "_post_json", new=AsyncMock(return_value=tavily_response)
    ):
        result = await WebSearchTool.run(query="three tallest mountains")

    assert result.success is True
    assert result.data["result_count"] == 2
    first = result.data["results"][0]
    assert set(first.keys()) == {"title", "url", "snippet", "source"}
    assert first["url"] == "https://en.wikipedia.org/wiki/List_of_highest_mountains"
    assert first["snippet"] == "Mount Everest is 8,848 m tall."
    assert first["source"] == "en.wikipedia.org"
    # Leading 'www.' is stripped for cleaner attribution.
    assert result.data["results"][1]["source"] == "britannica.com"


@pytest.mark.asyncio
async def test_tavily_search_dedupes_by_url():
    """Duplicate URLs are collapsed to a single result."""
    _set_provider("tavily", tavily_api_key="tvly-test")
    tavily_response = {
        "results": [
            {"title": "A", "url": "https://example.com/a", "content": "c1", "score": 0.9},
            {"title": "A2", "url": "https://example.com/a", "content": "c2", "score": 0.8},
        ]
    }

    with patch.object(
        WebSearchTool, "_post_json", new=AsyncMock(return_value=tavily_response)
    ):
        result = await WebSearchTool.run(query="dupes")

    assert result.success is True
    assert result.data["result_count"] == 1


@pytest.mark.asyncio
async def test_tavily_search_skips_items_without_url():
    """Results missing a url are dropped rather than producing empty entries."""
    _set_provider("tavily", tavily_api_key="tvly-test")
    tavily_response = {
        "results": [
            {"title": "No URL", "content": "no link here", "score": 0.5},
            {"title": "OK", "url": "https://example.com/ok", "content": "x", "score": 0.9},
        ]
    }

    with patch.object(
        WebSearchTool, "_post_json", new=AsyncMock(return_value=tavily_response)
    ):
        result = await WebSearchTool.run(query="missing url")

    assert result.success is True
    assert result.data["result_count"] == 1
    assert result.data["results"][0]["url"] == "https://example.com/ok"


@pytest.mark.asyncio
async def test_tavily_empty_results_returns_note():
    """An empty result list yields success with a helpful note."""
    _set_provider("tavily", tavily_api_key="tvly-test")
    with patch.object(WebSearchTool, "_post_json", new=AsyncMock(return_value={"results": []})):
        result = await WebSearchTool.run(query="nothing")

    assert result.success is True
    assert result.data["result_count"] == 0
    assert "note" in result.data


@pytest.mark.asyncio
async def test_zhipu_search_normalizes_results():
    """Zhipu results map into the same normalized shape."""
    _set_provider(
        "zhipu",
        zhipu_api_key="123456.abcdef",
        llm_provider="zhipu",
    )
    zhipu_response = {
        "search_result": [
            {
                "title": "Everest",
                "link": "https://en.wikipedia.org/wiki/Mount_Everest",
                "content": "8,848 m.",
                "media": "Wikipedia",
            }
        ]
    }

    with patch.object(
        WebSearchTool, "_post_json", new=AsyncMock(return_value=zhipu_response)
    ):
        result = await WebSearchTool.run(query="mount everest")

    assert result.success is True
    assert result.data["result_count"] == 1
    item = result.data["results"][0]
    assert item["url"] == "https://en.wikipedia.org/wiki/Mount_Everest"
    assert item["snippet"] == "8,848 m."
    assert item["source"] == "Wikipedia"


@pytest.mark.asyncio
async def test_unconfigured_provider_returns_error():
    """A backend without credentials returns success=False, never raises."""
    _set_provider("tavily", tavily_api_key="")
    result = await WebSearchTool.run(query="anything")

    assert result.success is False
    assert "not configured" in result.error


@pytest.mark.asyncio
async def test_empty_query_returns_error():
    """An empty query short-circuits before any network call."""
    _set_provider("tavily", tavily_api_key="tvly-test")
    result = await WebSearchTool.run(query="   ")

    assert result.success is False
    assert "No search query" in result.error


@pytest.mark.asyncio
async def test_http_failure_is_caught():
    """A failing HTTP call surfaces as a graceful error, not an exception."""
    _set_provider("tavily", tavily_api_key="tvly-test")

    async def _boom(*args, **kwargs):
        raise RuntimeError("network down")

    with patch.object(WebSearchTool, "_post_json", new=_boom):
        result = await WebSearchTool.run(query="will fail")

    assert result.success is False
    assert "Web search failed" in result.error
    assert "network down" in result.error
