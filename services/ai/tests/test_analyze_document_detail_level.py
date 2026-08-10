"""
Tests for analyze_document's detail_level-driven content window -
concise keeps a plain hard truncation, detailed/comprehensive summarize
in chunks instead of dropping material past the old fixed cutoff.
"""

import pytest

from app.agent import run_context
from app.agent.tools.analyze_document import AnalyzeDocumentTool, _DETAIL_MAX_CHARS


class _EchoSummarizer:
    """Returns a short, deterministic "summary" so chunk boundaries are verifiable."""

    async def chat(self, messages, tools=None):
        content = messages[0]["content"]
        chunk = content.split("\n\n", 1)[-1]
        return {"message": {"content": f"[summary of {len(chunk)} chars]"}}


class _FailingSummarizer:
    async def chat(self, messages, tools=None):
        raise RuntimeError("provider unavailable")


@pytest.mark.asyncio
async def test_short_content_is_never_touched():
    content = "short content"
    result, truncated, condensed = await AnalyzeDocumentTool._condense_for_context_window(content, "comprehensive")
    assert result == content
    assert truncated is False
    assert condensed is False


@pytest.mark.asyncio
async def test_concise_truncates_plainly_without_calling_the_llm():
    class _Exploding:
        async def chat(self, messages, tools=None):
            raise AssertionError("concise must not call the LLM")

    long_content = "x" * (_DETAIL_MAX_CHARS["concise"] + 5000)
    token = run_context.set_chat_provider(_Exploding())
    try:
        result, truncated, condensed = await AnalyzeDocumentTool._condense_for_context_window(long_content, "concise")
    finally:
        run_context.reset_chat_provider(token)

    assert truncated is True
    assert condensed is False
    assert result.endswith("... [truncated]")
    assert len(result) <= _DETAIL_MAX_CHARS["concise"] + len("... [truncated]")


@pytest.mark.asyncio
async def test_detailed_summarizes_instead_of_truncating():
    long_content = "y" * (_DETAIL_MAX_CHARS["detailed"] + 5000)
    token = run_context.set_chat_provider(_EchoSummarizer())
    try:
        result, truncated, condensed = await AnalyzeDocumentTool._condense_for_context_window(long_content, "detailed")
    finally:
        run_context.reset_chat_provider(token)

    assert truncated is True
    assert condensed is True
    assert "[summary of" in result


@pytest.mark.asyncio
async def test_comprehensive_has_a_higher_ceiling_than_detailed():
    assert _DETAIL_MAX_CHARS["comprehensive"] > _DETAIL_MAX_CHARS["detailed"] > _DETAIL_MAX_CHARS["concise"]


@pytest.mark.asyncio
async def test_summarization_failure_falls_back_to_a_raw_excerpt_not_dropped_content():
    """A chunk that fails to summarize (provider error) must still
    contribute something to the final content, not vanish silently."""
    long_content = "z" * (_DETAIL_MAX_CHARS["detailed"] + 5000)
    token = run_context.set_chat_provider(_FailingSummarizer())
    try:
        result, truncated, condensed = await AnalyzeDocumentTool._condense_for_context_window(long_content, "detailed")
    finally:
        run_context.reset_chat_provider(token)

    assert truncated is True
    assert condensed is True
    assert result  # not empty - raw excerpts were used as the fallback
    assert "z" in result


@pytest.mark.asyncio
async def test_condensed_output_never_exceeds_the_level_s_ceiling():
    long_content = "w" * (_DETAIL_MAX_CHARS["comprehensive"] * 2)
    token = run_context.set_chat_provider(_EchoSummarizer())
    try:
        result, _, _ = await AnalyzeDocumentTool._condense_for_context_window(long_content, "comprehensive")
    finally:
        run_context.reset_chat_provider(token)

    assert len(result) <= _DETAIL_MAX_CHARS["comprehensive"] + len("... [truncated]")


@pytest.mark.asyncio
async def test_unknown_detail_level_falls_back_to_detailed_ceiling():
    # Longer than "concise"'s ceiling (15000) but within "detailed"'s
    # (30000) - only truncates if the fallback used concise's ceiling.
    assert _DETAIL_MAX_CHARS["concise"] < 20000 < _DETAIL_MAX_CHARS["detailed"]
    content = "a" * 20000
    result, truncated, condensed = await AnalyzeDocumentTool._condense_for_context_window(content, "not-a-real-level")

    assert truncated is False
    assert condensed is False
    assert result == content
