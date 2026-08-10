"""
Tests for select_source_image - confidence-gated reuse of an already-
extracted image (never generation). Replaces the old dead-end
generate_image_prompt tool.
"""

import json
import pytest

from app.agent import run_context
from app.agent.tools.select_source_image import SelectSourceImageTool


class _ScriptedProvider:
    def __init__(self, content: str):
        self.content = content

    async def chat(self, messages, tools=None):
        return {"message": {"content": self.content}}


CATALOG = json.dumps([
    {"id": "doc-report.pdf-p1-0", "context": "A bar chart showing quarterly revenue growth."},
    {"id": "web-0-https://x.test/team.jpg", "context": "Team: Company leadership photo."},
])


async def _run_with_response(content: str, **kwargs):
    token = run_context.set_chat_provider(_ScriptedProvider(content))
    try:
        return await SelectSourceImageTool.run(**kwargs)
    finally:
        run_context.reset_chat_provider(token)


@pytest.mark.asyncio
async def test_empty_catalog_short_circuits_without_calling_the_llm():
    """No candidate images -> no LLM call at all, always "not selected"."""
    class _ExplodingProvider:
        async def chat(self, messages, tools=None):
            raise AssertionError("should not call the LLM when the catalog is empty")

    token = run_context.set_chat_provider(_ExplodingProvider())
    try:
        result = await SelectSourceImageTool.run(
            slide_index=0, slide_topic="Q3 revenue", catalog_json="[]",
        )
    finally:
        run_context.reset_chat_provider(token)

    assert result.success is True
    assert result.data["selected"] is False
    assert result.data["imageId"] is None


@pytest.mark.asyncio
async def test_confident_match_is_selected():
    response = json.dumps({
        "selected": True,
        "imageId": "doc-report.pdf-p1-0",
        "reason": "Directly shows the quarterly revenue chart this slide discusses.",
    })
    result = await _run_with_response(
        response, slide_index=2, slide_topic="Quarterly revenue growth", catalog_json=CATALOG,
    )
    assert result.success is True
    assert result.data["selected"] is True
    assert result.data["imageId"] == "doc-report.pdf-p1-0"
    assert result.data["slideIndex"] == 2


@pytest.mark.asyncio
async def test_no_confident_match_selects_nothing():
    response = json.dumps({"selected": False, "imageId": None, "reason": "Nothing in the catalog matches this topic."})
    result = await _run_with_response(
        response, slide_index=0, slide_topic="Market outlook for 2027", catalog_json=CATALOG,
    )
    assert result.success is True
    assert result.data["selected"] is False
    assert result.data["imageId"] is None


@pytest.mark.asyncio
async def test_hallucinated_image_id_is_rejected():
    """Defends against the LLM inventing an id that was never in the catalog it was given."""
    response = json.dumps({"selected": True, "imageId": "not-a-real-id", "reason": "..."})
    result = await _run_with_response(
        response, slide_index=0, slide_topic="Revenue", catalog_json=CATALOG,
    )
    assert result.success is True
    assert result.data["selected"] is False
    assert result.data["imageId"] is None


@pytest.mark.asyncio
async def test_unparseable_response_defaults_to_no_selection():
    result = await _run_with_response(
        "I'm not sure how to answer that.", slide_index=0, slide_topic="Revenue", catalog_json=CATALOG,
    )
    assert result.success is True
    assert result.data["selected"] is False


@pytest.mark.asyncio
async def test_catalog_data_field_is_never_sent_to_the_llm():
    """The catalog_json argument (built by the loop's auto-injection) must
    only ever contain lightweight metadata - this test guards the tool's own
    contract even though stripping the data field is actually the loop's
    job, by asserting the prompt sent to the LLM never contains the raw
    image bytes even if a caller mistakenly included them."""
    heavy_catalog = json.dumps([
        {"id": "doc-x", "context": "A photo", "data": "data:image/png;base64," + "A" * 5000},
    ])

    captured = {}

    class _CapturingProvider:
        async def chat(self, messages, tools=None):
            captured["prompt"] = messages[0]["content"]
            return {"message": {"content": '{"selected": false, "imageId": null, "reason": "n/a"}'}}

    token = run_context.set_chat_provider(_CapturingProvider())
    try:
        await SelectSourceImageTool.run(slide_index=0, slide_topic="x", catalog_json=heavy_catalog)
    finally:
        run_context.reset_chat_provider(token)

    assert "base64" not in captured["prompt"]
    assert "A" * 100 not in captured["prompt"]
