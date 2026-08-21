"""
Tests for the web_search agent tool and its pluggable backends.

HTTP backends are exercised by mocking the shared `post_json` helper, and the
SDK-based native backends by feeding their parsers a canned response object, so
no test makes a real network call. The invariant every case asserts is that
results reach the agent loop in the same normalized
{title, url, snippet, source} shape regardless of which backend produced them.
"""

import pytest
from unittest.mock import AsyncMock, patch

from app.agent import run_context
from app.agent.tools import search_backends
from app.agent.tools.web_search import WebSearchTool
from app.ai_settings import SearchConfig
from app.llm.provider import ProviderCredentials


@pytest.fixture(autouse=True)
def _clear_run_context():
    """Each test starts with no configuration installed on the run context."""
    token = run_context.set_search_config(None)
    yield
    run_context.reset_search_config(token)


def _use(config: SearchConfig):
    run_context.set_search_config(config)


TAVILY = SearchConfig(backend="tavily", api_key="tvly-test")
FIRECRAWL = SearchConfig(backend="firecrawl", api_key="fc-test")
ZHIPU = SearchConfig(
    backend="native",
    model=ProviderCredentials("zhipu", "glm-4.7", "123456.abcdef"),
)


class _Obj:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


# ── Tavily ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tavily_search_normalizes_results():
    """Tavily results map into {title, url, snippet, source} with a host source."""
    _use(TAVILY)
    response = {
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

    with patch.object(search_backends, "post_json", new=AsyncMock(return_value=response)):
        result = await WebSearchTool.run(query="three tallest mountains")

    assert result.success is True
    assert result.data["result_count"] == 2
    first = result.data["results"][0]
    assert set(first.keys()) == {"title", "url", "snippet", "source", "published"}
    assert first["url"] == "https://en.wikipedia.org/wiki/List_of_highest_mountains"
    assert first["snippet"] == "Mount Everest is 8,848 m tall."
    assert first["source"] == "en.wikipedia.org"
    # Leading 'www.' is stripped for cleaner attribution.
    assert result.data["results"][1]["source"] == "britannica.com"


@pytest.mark.asyncio
async def test_results_are_deduped_by_url():
    """Duplicate URLs are collapsed, whichever backend produced them."""
    _use(TAVILY)
    response = {
        "results": [
            {"title": "A", "url": "https://example.com/a", "content": "c1"},
            {"title": "A2", "url": "https://example.com/a", "content": "c2"},
        ]
    }

    with patch.object(search_backends, "post_json", new=AsyncMock(return_value=response)):
        result = await WebSearchTool.run(query="dupes")

    assert result.data["result_count"] == 1


@pytest.mark.asyncio
async def test_results_without_a_url_are_dropped():
    """A result with no link cannot be cited, so it is not surfaced."""
    _use(TAVILY)
    response = {
        "results": [
            {"title": "No URL", "content": "no link here"},
            {"title": "OK", "url": "https://example.com/ok", "content": "x"},
        ]
    }

    with patch.object(search_backends, "post_json", new=AsyncMock(return_value=response)):
        result = await WebSearchTool.run(query="missing url")

    assert result.data["result_count"] == 1
    assert result.data["results"][0]["url"] == "https://example.com/ok"


@pytest.mark.asyncio
async def test_empty_results_return_a_note_not_a_failure():
    """The agent should continue on its own knowledge, not treat this as an error."""
    _use(TAVILY)
    with patch.object(search_backends, "post_json", new=AsyncMock(return_value={"results": []})):
        result = await WebSearchTool.run(query="nothing")

    assert result.success is True
    assert result.data["result_count"] == 0
    assert "note" in result.data


# ── Zhipu (native) ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_zhipu_search_normalizes_results():
    """Zhipu results map into the same normalized shape."""
    _use(ZHIPU)
    response = {
        "search_result": [{
            "title": "Everest",
            "link": "https://en.wikipedia.org/wiki/Mount_Everest",
            "content": "8,848 m.",
            "media": "Wikipedia",
        }]
    }

    with patch.object(search_backends, "post_json", new=AsyncMock(return_value=response)):
        result = await WebSearchTool.run(query="mount everest")

    assert result.success is True
    item = result.data["results"][0]
    assert item["url"] == "https://en.wikipedia.org/wiki/Mount_Everest"
    assert item["snippet"] == "8,848 m."
    assert item["source"] == "Wikipedia"


def test_zhipu_jwt_requires_an_id_secret_key():
    """A plain key cannot sign the bearer token the search endpoint needs."""
    with pytest.raises(ValueError):
        search_backends.build_zhipu_jwt("plainkey")


def test_zhipu_parser_tolerates_a_single_object_result():
    """The API returns a bare object rather than a list for some queries."""
    parsed = search_backends.parse_zhipu_response(
        {"search_result": {"title": "T", "link": "https://x.test/a", "content": "c"}}
    )

    assert len(parsed) == 1
    assert parsed[0]["url"] == "https://x.test/a"


# ── Firecrawl ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_firecrawl_prefers_scraped_markdown_over_the_description():
    """Scraped page content grounds slide claims far better than a search blurb."""
    _use(FIRECRAWL)
    response = {
        "success": True,
        "data": {"web": [{
            "url": "https://example.com/report",
            "title": "Annual Report",
            "description": "A short blurb.",
            "markdown": "# Annual Report\n\nRevenue grew 18% to $4.2bn.",
        }]},
    }

    with patch.object(search_backends, "post_json", new=AsyncMock(return_value=response)):
        result = await WebSearchTool.run(query="annual report")

    assert result.success is True
    assert "Revenue grew 18%" in result.data["results"][0]["snippet"]
    assert result.data["results"][0]["source"] == "example.com"


def test_firecrawl_parser_accepts_the_older_flat_data_shape():
    """Older Firecrawl responses return `data` as a list rather than a dict."""
    parsed = search_backends.parse_firecrawl_response(
        {"data": [{"url": "https://x.test/a", "title": "A", "description": "d"}]}
    )

    assert parsed[0]["snippet"] == "d"


def test_firecrawl_snippets_are_capped():
    """A scraped page can be enormous; the prompt must not absorb all of it."""
    parsed = search_backends.parse_firecrawl_response(
        {"data": {"web": [{"url": "https://x.test/a", "title": "A", "markdown": "x" * 9000}]}}
    )

    assert len(parsed[0]["snippet"]) == search_backends.FIRECRAWL_SNIPPET_CHARS


def test_firecrawl_extracts_candidate_images_from_scraped_markdown():
    """Reused for select_source_image's catalog - never generated, only
    surfaced as a candidate the LLM still has to confidently confirm."""
    markdown = (
        "# Report\n\n"
        "![Revenue chart](https://example.com/chart.png)\n\n"
        "Some text here.\n\n"
        "![Team photo](https://example.com/team.jpg \"caption\")"
    )
    parsed = search_backends.parse_firecrawl_response(
        {"data": {"web": [{"url": "https://example.com/report", "title": "Report", "markdown": markdown}]}}
    )
    assert parsed[0]["images"] == ["https://example.com/chart.png"]


def test_firecrawl_images_absent_when_markdown_has_none():
    parsed = search_backends.parse_firecrawl_response(
        {"data": {"web": [{"url": "https://x.test/a", "title": "A", "markdown": "just text, no images"}]}}
    )
    assert parsed[0]["images"] == []


def test_firecrawl_images_are_capped_per_result():
    markdown = "\n".join(f"![img{i}](https://x.test/{i}.png)" for i in range(10))
    parsed = search_backends.parse_firecrawl_response(
        {"data": {"web": [{"url": "https://x.test/a", "title": "A", "markdown": markdown}]}}
    )
    assert len(parsed[0]["images"]) == search_backends.MAX_IMAGES_PER_RESULT


def test_other_backends_report_no_images():
    """Tavily/Zhipu/Anthropic/OpenAI never claim image candidates they don't have."""
    parsed = search_backends.parse_tavily_response(
        {"results": [{"url": "https://x.test/a", "title": "A", "content": "c"}]}
    )
    assert "images" not in parsed[0] or parsed[0].get("images") == []


# ── Anthropic (native) ─────────────────────────────────────────────────


def test_anthropic_citations_become_normalized_results():
    """Claude's search results are encrypted; the citations carry the readable text."""
    response = _Obj(content=[
        _Obj(
            type="text",
            text="Everest is 8,848 m.",
            citations=[_Obj(
                type="web_search_result_location",
                url="https://www.nationalgeographic.com/everest",
                title="Everest",
                cited_text="Everest rises to 8,848 metres.",
            )],
        ),
        _Obj(type="web_search_tool_result", content=[]),
    ])

    parsed = search_backends.parse_anthropic_citations(response)

    assert parsed == [{
        "title": "Everest",
        "url": "https://www.nationalgeographic.com/everest",
        "snippet": "Everest rises to 8,848 metres.",
        "source": "nationalgeographic.com",
        # Anthropic dates the tool-result block, not the citation, and only
        # ever relatively ("3 days ago") - so this stays empty.
        "published": "",
    }]


def test_anthropic_parser_ignores_text_blocks_with_no_citations():
    """Uncited prose is the model talking, not a source."""
    response = _Obj(content=[_Obj(type="text", text="Let me search.", citations=None)])

    assert search_backends.parse_anthropic_citations(response) == []


# ── OpenAI (native) ────────────────────────────────────────────────────


def test_openai_annotations_use_the_cited_span_as_the_snippet():
    """OpenAI reports offsets rather than quotes, so the answer text is sliced."""
    content = "Everest is the tallest mountain at 8,848 m above sea level."
    response = _Obj(choices=[_Obj(message=_Obj(
        content=content,
        annotations=[{
            "type": "url_citation",
            "url_citation": {
                "url": "https://example.com/everest",
                "title": "Everest",
                "start_index": 0,
                "end_index": len(content),
            },
        }],
    ))])

    parsed = search_backends.parse_openai_annotations(response)

    assert parsed[0]["url"] == "https://example.com/everest"
    assert parsed[0]["snippet"] == content
    assert parsed[0]["source"] == "example.com"


def test_openai_parser_handles_a_response_with_no_annotations():
    """A search model that answered from memory yields no sources, not a crash."""
    response = _Obj(choices=[_Obj(message=_Obj(content="No sources.", annotations=None))])

    assert search_backends.parse_openai_annotations(response) == []


# ── Tool-level behaviour ───────────────────────────────────────────────


@pytest.mark.unconfigured_workspace
@pytest.mark.asyncio
async def test_unconfigured_backend_returns_an_actionable_error():
    """A workspace with no backend must degrade rather than raise.

    Genuinely unconfigured, not merely lacking a search-capable model: now
    that OpenAI search is not gated on particular model names, a workspace
    with any chat model configured can search with it, so the only way to
    reach this path is to have configured nothing at all.
    """
    result = await WebSearchTool.run(query="anything")

    assert result.success is False
    assert "not available" in result.error
    assert "Settings" in result.error


@pytest.mark.asyncio
async def test_empty_query_short_circuits_before_any_network_call():
    _use(TAVILY)
    result = await WebSearchTool.run(query="   ")

    assert result.success is False
    assert "No search query" in result.error


@pytest.mark.asyncio
async def test_backend_failure_is_caught():
    """A failing backend surfaces as a graceful error, not an exception."""
    _use(TAVILY)

    async def _boom(*args, **kwargs):
        raise RuntimeError("network down")

    with patch.object(search_backends, "post_json", new=_boom):
        result = await WebSearchTool.run(query="will fail")

    assert result.success is False
    assert "Web search failed" in result.error
    assert "network down" in result.error


@pytest.mark.asyncio
async def test_unknown_backend_is_reported_rather_than_crashing():
    """Guards against a config row written by a newer version of the service."""
    _use(SearchConfig(backend="bing", api_key="k"))

    result = await WebSearchTool.run(query="anything")

    assert result.success is False
    assert "bing" in result.error


@pytest.mark.asyncio
async def test_concurrent_runs_do_not_share_a_backend():
    """Two organisations generating at once must not see each other's config."""
    import asyncio

    seen: dict[str, str] = {}

    async def run_with(name: str, config: SearchConfig):
        run_context.set_search_config(config)
        await asyncio.sleep(0)
        seen[name] = run_context.get_search_config().backend

    await asyncio.gather(
        run_with("a", TAVILY),
        run_with("b", FIRECRAWL),
    )

    assert seen == {"a": "tavily", "b": "firecrawl"}


@pytest.mark.asyncio
async def test_openai_search_passes_web_search_options_via_extra_body():
    """Regression: a named kwarg the installed SDK does not know is a TypeError.

    openai==1.30.0 predates web_search_options, so passing it as a named
    argument failed before any request was made -
    "AsyncCompletions.create() got an unexpected keyword argument". extra_body
    is forwarded to the API verbatim on every SDK version.
    """
    captured: dict = {}

    class _Completions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return _Obj(choices=[_Obj(message=_Obj(content="", annotations=None))])

    class _Client:
        def __init__(self, **kwargs):
            self.chat = _Obj(completions=_Completions())

    import openai
    with patch.object(openai, "AsyncOpenAI", _Client):
        await search_backends._openai_search(
            SearchConfig(
                backend="native",
                model=ProviderCredentials("openai", "gpt-4o-search-preview", "sk-test"),
            ),
            "how tall is everest",
            5,
        )

    assert "web_search_options" not in captured, "must not be a named argument"
    assert captured["extra_body"] == {"web_search_options": {}}
    assert captured["model"] == "gpt-4o-search-preview"


def test_openai_parser_accepts_dict_shaped_annotations():
    """Older SDKs surface annotations as plain dicts, not typed models."""
    content = "Everest is 8,849 m."
    response = _Obj(choices=[_Obj(message=_Obj(
        content=content,
        annotations=[{
            "type": "url_citation",
            "url_citation": {
                "url": "https://example.com/everest",
                "title": "Everest",
                "start_index": 0,
                "end_index": len(content),
            },
        }],
    ))])

    parsed = search_backends.parse_openai_annotations(response)

    assert parsed[0]["url"] == "https://example.com/everest"
    assert parsed[0]["snippet"] == content


# ── Publication dates ──────────────────────────────────────────────────
#
# Only some backends date a page, and the ones that do are inconsistent about
# the format. A reference in the rendered deck prints the date when there is
# one, so the reader can see how current a figure is - which means a date that
# cannot be trusted must not be invented.


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2026-03-11", "2026-03-11"),
        ("2026-03-11T09:30:00Z", "2026-03-11"),
        ("2026-03-11 09:30:00", "2026-03-11"),
        ("  2026-03-11  ", "2026-03-11"),
        # Relative ages and free text say nothing a reader can act on.
        ("3 days ago", ""),
        ("March 2026", ""),
        ("2026-13-11", ""),
        ("2026-03-99", ""),
        ("", ""),
        (None, ""),
        (20260311, ""),
    ],
)
def test_published_date_only_accepts_a_real_calendar_date(value, expected):
    assert search_backends.published_date(value) == expected


def test_tavily_carries_a_publication_date_when_it_has_one():
    parsed = search_backends.parse_tavily_response({
        "results": [
            {"url": "https://a.com", "title": "A", "content": "x",
             "published_date": "2026-03-11T00:00:00Z"},
            {"url": "https://b.com", "title": "B", "content": "y"},
        ]
    })
    assert [r["published"] for r in parsed] == ["2026-03-11", ""]


def test_zhipu_carries_a_publication_date_when_it_has_one():
    parsed = search_backends.parse_zhipu_response({
        "search_result": [
            {"link": "https://a.com", "title": "A", "content": "x",
             "publish_date": "2026-03-11"},
        ]
    })
    assert parsed[0]["published"] == "2026-03-11"


def test_firecrawl_reads_the_date_off_the_scraped_page_metadata():
    parsed = search_backends.parse_firecrawl_response({
        "data": {"web": [
            {"url": "https://a.com", "title": "A", "description": "x",
             "metadata": {"publishedTime": "2026-03-11T08:00:00+00:00"}},
        ]}
    })
    assert parsed[0]["published"] == "2026-03-11"


def test_firecrawl_without_metadata_claims_no_date():
    parsed = search_backends.parse_firecrawl_response({
        "data": {"web": [{"url": "https://a.com", "title": "A", "description": "x"}]}
    })
    assert parsed[0]["published"] == ""


# ── OpenAI: the Responses API route ────────────────────────────────────


class _RecordingOpenAI:
    """A client that records the call and answers with one cited result."""

    RESPONSES_ANSWER = _Obj(output=[_Obj(content=[_Obj(
        type="output_text",
        text="Everest is 8,849 m tall.",
        annotations=[{
            "type": "url_citation", "url": "https://nat-geo.example/everest",
            "title": "Everest", "start_index": 0, "end_index": 24,
        }],
    )])])

    def __init__(self, **kwargs):
        self.seen: dict = {}
        outer = self

        class _Responses:
            async def create(self, **kwargs):
                outer.seen["responses"] = kwargs
                if outer.responses_error:
                    raise outer.responses_error
                return outer.RESPONSES_ANSWER

        class _Completions:
            async def create(self, **kwargs):
                outer.seen["chat"] = kwargs
                return _Obj(choices=[_Obj(message=_Obj(content="", annotations=None))])

        self.responses = _Responses()
        self.chat = _Obj(completions=_Completions())
        self.responses_error: Exception | None = None
        _RecordingOpenAI.last = self


@pytest.mark.asyncio
async def test_a_current_model_searches_through_the_responses_api():
    """Regression: the '-search-preview' models this used to need are retired.

    Chat completions' web_search_options only ever worked on those, so after
    they shut down on 23 July 2026 there was no model left that could search
    this way. The Responses API's web_search tool is model-agnostic.
    """
    import openai

    with patch.object(openai, "AsyncOpenAI", _RecordingOpenAI):
        results = await search_backends._openai_search(
            SearchConfig(
                backend="native",
                model=ProviderCredentials("openai", "gpt-5.6-terra", "sk-test"),
            ),
            "how tall is everest",
            5,
        )

    seen = _RecordingOpenAI.last.seen
    assert "chat" not in seen, "must not fall back to the retired route"
    assert seen["responses"]["model"] == "gpt-5.6-terra"
    # A named argument the installed SDK does not know is a TypeError before
    # any request is made; extra_body is forwarded verbatim on every version.
    assert seen["responses"]["extra_body"]["tools"] == [{"type": "web_search"}]
    assert results == [{
        "title": "Everest",
        "url": "https://nat-geo.example/everest",
        "snippet": "Everest is 8,849 m tall.",
        "source": "nat-geo.example",
        "published": "",
    }]


@pytest.mark.asyncio
async def test_a_legacy_search_model_still_takes_the_chat_completions_route():
    """A deployment still pointed at a search-preview model keeps working."""
    import openai

    with patch.object(openai, "AsyncOpenAI", _RecordingOpenAI):
        await search_backends._openai_search(
            SearchConfig(
                backend="native",
                model=ProviderCredentials("openai", "gpt-4o-search-preview", "sk-test"),
            ),
            "how tall is everest",
            5,
        )

    seen = _RecordingOpenAI.last.seen
    assert "responses" not in seen
    assert seen["chat"]["extra_body"] == {"web_search_options": {}}


@pytest.mark.asyncio
async def test_an_endpoint_without_a_responses_api_falls_back():
    """An OpenAI-compatible proxy may implement chat completions and nothing else."""
    import openai

    def _factory(**kwargs):
        client = _RecordingOpenAI(**kwargs)
        client.responses_error = Exception("Error code: 404 - {'error': 'Not Found'}")
        return client

    with patch.object(openai, "AsyncOpenAI", _factory):
        await search_backends._openai_search(
            SearchConfig(
                backend="native",
                model=ProviderCredentials(
                    "openai", "local-model", "sk-test", base_url="http://proxy/v1"
                ),
            ),
            "how tall is everest",
            5,
        )

    assert "chat" in _RecordingOpenAI.last.seen


@pytest.mark.asyncio
async def test_a_real_search_failure_is_not_hidden_by_the_fallback():
    """Only "no such endpoint" falls back - a bad key must surface as itself."""
    import openai

    def _factory(**kwargs):
        client = _RecordingOpenAI(**kwargs)
        client.responses_error = Exception("Incorrect API key provided: sk-***.")
        return client

    with patch.object(openai, "AsyncOpenAI", _factory), \
            pytest.raises(Exception, match="Incorrect API key"):
        await search_backends._openai_search(
            SearchConfig(
                backend="native",
                model=ProviderCredentials("openai", "gpt-5.6-terra", "sk-test"),
            ),
            "how tall is everest",
            5,
        )


def test_the_responses_parser_reads_typed_annotations_too():
    """Newer SDKs return models, not dicts, and nest nothing under url_citation."""
    parsed = search_backends.parse_openai_response_annotations(
        _Obj(output=[_Obj(content=[_Obj(
            text="Everest is 8,849 m tall.",
            annotations=[_Obj(
                type="url_citation", url="https://a.example/e", title="E",
                start_index=0, end_index=24,
            )],
        )])])
    )

    assert [r["url"] for r in parsed] == ["https://a.example/e"]
    assert parsed[0]["snippet"] == "Everest is 8,849 m tall."


@pytest.mark.asyncio
async def test_a_missing_model_is_not_mistaken_for_a_missing_endpoint():
    """Both are 404s. Only one is worth a second, equally doomed request."""
    import openai

    def _factory(**kwargs):
        client = _RecordingOpenAI(**kwargs)
        client.responses_error = Exception(
            "Error code: 404 - The model 'gpt-5.7' does not exist."
        )
        return client

    with patch.object(openai, "AsyncOpenAI", _factory), \
            pytest.raises(Exception, match="does not exist"):
        await search_backends._openai_search(
            SearchConfig(
                backend="native",
                model=ProviderCredentials("openai", "gpt-5.7", "sk-test"),
            ),
            "how tall is everest",
            5,
        )

    assert "chat" not in _RecordingOpenAI.last.seen


@pytest.mark.asyncio
async def test_an_sdk_without_a_responses_api_falls_back():
    """A pinned-back openai SDK raises AttributeError before any request."""
    import openai

    class _OldSdk(_RecordingOpenAI):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            del self.responses

    with patch.object(openai, "AsyncOpenAI", _OldSdk):
        await search_backends._openai_search(
            SearchConfig(
                backend="native",
                model=ProviderCredentials("openai", "gpt-5.6-terra", "sk-test"),
            ),
            "how tall is everest",
            5,
        )

    assert "chat" in _RecordingOpenAI.last.seen
