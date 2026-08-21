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


@pytest.mark.asyncio
async def test_unconfigured_backend_returns_an_actionable_error(monkeypatch):
    """Simple mode, and orgs with no backend, must degrade rather than raise."""
    from app.config import settings as env_settings
    monkeypatch.setattr(env_settings, "zhipu_api_key", "")
    monkeypatch.setattr(env_settings, "web_search_provider", "zhipu")
    monkeypatch.setattr(env_settings, "tavily_api_key", "")

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


# ── Recency filter ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_recency_is_forwarded_into_the_zhipu_request_body():
    """The whole point of the argument: a recency-qualified query must
    actually ask Zhipu for fresher results, not just say so in the prompt."""
    _use(ZHIPU)
    mock_post = AsyncMock(return_value={"search_result": []})

    with patch.object(search_backends, "post_json", new=mock_post):
        await WebSearchTool.run(query="Green Climate Fund board meetings", recency="year")

    body = mock_post.call_args.args[2]
    assert body["search_recency_filter"] == "year"


@pytest.mark.asyncio
async def test_recency_is_forwarded_into_the_tavily_request_body():
    _use(TAVILY)
    mock_post = AsyncMock(return_value={"results": []})

    with patch.object(search_backends, "post_json", new=mock_post):
        await WebSearchTool.run(query="Green Climate Fund board meetings", recency="month")

    body = mock_post.call_args.args[2]
    assert body["time_range"] == "month"


@pytest.mark.asyncio
async def test_omitting_recency_sends_no_filter():
    """Unfiltered is still the default - the argument only narrows on request."""
    _use(ZHIPU)
    mock_post = AsyncMock(return_value={"search_result": []})

    with patch.object(search_backends, "post_json", new=mock_post):
        await WebSearchTool.run(query="anything")

    body = mock_post.call_args.args[2]
    assert "search_recency_filter" not in body


@pytest.mark.asyncio
async def test_an_off_schema_recency_value_is_dropped_not_sent_upstream():
    """A model can emit anything as a string argument - 'recent', '2026', a
    typo'd enum value. It must never reach Zhipu/Tavily's request body, and
    the call must still succeed rather than fail on an upstream 400."""
    _use(ZHIPU)
    mock_post = AsyncMock(return_value={"search_result": []})

    with patch.object(search_backends, "post_json", new=mock_post):
        result = await WebSearchTool.run(query="anything", recency="recent")

    body = mock_post.call_args.args[2]
    assert "search_recency_filter" not in body
    assert result.success is True
    assert result.data["recency"] == ""


@pytest.mark.asyncio
async def test_recency_requested_against_an_unsupporting_backend_is_flagged():
    """Firecrawl has no recency filter. Silently dropping the argument with
    no signal would look like the feature is broken rather than unsupported
    on this config."""
    _use(FIRECRAWL)
    response = {"data": {"web": [{"url": "https://a.com", "title": "A", "description": "x"}]}}

    with patch.object(search_backends, "post_json", new=AsyncMock(return_value=response)):
        result = await WebSearchTool.run(query="anything", recency="week")

    assert result.success is True
    assert "does not support filtering by recency" in result.data["note"]


class TestRecencyIsPartOfTheAlreadyRunCacheKey:
    """loop._search_already_run must not hand back an earlier UNFILTERED
    search's results when the model deliberately re-issues the same query
    text WITH a recency filter to get fresher results - the exact move the
    Deep-mode research prompt instructs for a 'most recent' style request.
    Matching on query text alone would silently defeat that instruction by
    serving the stale cached answer instead of running the filtered search.
    """

    RESEARCH = [
        {"query": "Green Climate Fund recent projects", "recency": "",
         "title": "GCF portfolio (2023)", "url": "https://gcf.example/2023",
         "snippet": "2023 approved projects", "published": "2023-11-02"},
    ]

    def _loop(self):
        from app.agent.loop import AgentLoop

        return AgentLoop.__new__(AgentLoop)

    def test_an_unfiltered_repeat_is_still_served_from_cache(self):
        """Unchanged baseline behaviour: same query, no recency either time."""
        cached = self._loop()._answer_from_context(
            "web_search",
            {"query": "Green Climate Fund recent projects"},
            {"research": self.RESEARCH},
        )

        assert cached is not None
        assert [r["url"] for r in cached.data["results"]] == ["https://gcf.example/2023"]

    def test_a_recency_qualified_requery_is_not_served_the_stale_cache(self):
        """The bug: same query text, now WITH recency='year'. Before the fix
        this matched on text alone and handed back the 2023-only results
        without ever calling the backend with the filter applied."""
        cached = self._loop()._answer_from_context(
            "web_search",
            {"query": "Green Climate Fund recent projects", "recency": "year"},
            {"research": self.RESEARCH},
        )

        assert cached is None

    def test_a_second_search_with_the_same_recency_is_still_cached(self):
        """The fix narrows the match, it doesn't turn it off: once a
        recency-filtered search has actually run and been recorded, asking
        the identical question again should still be answered from it."""
        research = [
            {"query": "Green Climate Fund recent projects", "recency": "year",
             "title": "GCF Board 45", "url": "https://gcf.example/board-45",
             "snippet": "Summer 2026 approvals", "published": "2026-06-01"},
        ]

        cached = self._loop()._answer_from_context(
            "web_search",
            {"query": "Green Climate Fund recent projects", "recency": "year"},
            {"research": research},
        )

        assert cached is not None
        assert [r["url"] for r in cached.data["results"]] == ["https://gcf.example/board-45"]


# ── Stale years in the query ───────────────────────────────────────────

# A model reaches for the year its training data ended in whenever it means
# "now". The system prompt states today's date, and it still happens: "latest
# Green Climate Fund approved projects 2023", asked in 2026, is a real query
# this tool received. The search engine honours the year and returns exactly
# the stale pages "latest" was meant to exclude.

import datetime  # noqa: E402

from app.agent.tools.web_search import strip_stale_year  # noqa: E402

TODAY = datetime.date(2026, 8, 21)


@pytest.mark.parametrize(
    "query,expected,removed",
    [
        (
            "latest Green Climate Fund approved projects 2023",
            "latest Green Climate Fund approved projects",
            "2023",
        ),
        # The dangling connective the year leaves behind goes with it.
        ("most recent GCF approvals in 2024", "most recent GCF approvals", "2024"),
        # No recency word: a deliberate question about a past year, untouched.
        ("Green Climate Fund approvals in 2019", "Green Climate Fund approvals in 2019", ""),
        # The current year is not stale, and neither is a later one.
        ("latest GCF projects 2026", "latest GCF projects 2026", ""),
        ("latest GCF pipeline through 2030", "latest GCF pipeline through 2030", ""),
        # Nothing to do at all.
        ("Green Climate Fund portfolio size", "Green Climate Fund portfolio size", ""),
    ],
)
def test_a_past_year_survives_only_when_the_query_is_not_asking_for_the_latest(
    query, expected, removed
):
    assert strip_stale_year(query, TODAY) == (expected, removed)


def test_a_query_that_is_only_a_stale_year_is_left_intact():
    """Emptying the query would turn a bad search into no search."""
    assert strip_stale_year("latest 2023", TODAY) == ("latest 2023", "2023")


@pytest.mark.asyncio
async def test_the_tool_searches_the_corrected_query_and_says_so():
    _use(TAVILY)
    mock_post = AsyncMock(return_value={"results": []})

    with patch.object(search_backends, "post_json", new=mock_post):
        result = await WebSearchTool.run(
            query="latest Green Climate Fund approved projects 2023"
        )

    body = mock_post.call_args.args[2]
    assert "2023" not in body["query"]
    # The intent behind the year is preserved, in the form the backend can act on.
    assert body["time_range"] == "year"
    assert result.data["recency"] == "year"
    assert "2023" in result.data["note"] and "past" in result.data["note"]


@pytest.mark.asyncio
async def test_an_explicit_recency_is_not_overridden_by_the_correction():
    _use(TAVILY)
    mock_post = AsyncMock(return_value={"results": []})

    with patch.object(search_backends, "post_json", new=mock_post):
        await WebSearchTool.run(
            query="latest GCF board decisions 2023", recency="week"
        )

    assert mock_post.call_args.args[2]["time_range"] == "week"


@pytest.mark.asyncio
async def test_a_deliberate_historical_query_reaches_the_backend_unchanged():
    """The correction must not rewrite a question that meant its year."""
    _use(TAVILY)
    mock_post = AsyncMock(return_value={"results": []})

    with patch.object(search_backends, "post_json", new=mock_post):
        result = await WebSearchTool.run(query="GCF projects approved in 2019")

    assert mock_post.call_args.args[2]["query"] == "GCF projects approved in 2019"
    assert "2019" not in (result.data.get("note") or "")
