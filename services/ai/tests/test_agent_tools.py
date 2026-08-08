"""
Tests for agent tool implementations.
"""

import pytest
import json
from app.agent.tools.validate_slide_json import ValidateSlideJsonTool


@pytest.mark.asyncio
async def test_validate_valid_slide():
    """Test validation of a well-formed slide JSON."""
    slide = {
        "slideIndex": 0,
        "layoutId": "1",
        "layoutName": "Title Slide",
        "contents": [
            {
                "type": "text",
                "placeholderIdx": 0,
                "runs": [{"text": "Hello World", "bold": True}],
            }
        ],
    }

    result = await ValidateSlideJsonTool.run(
        slide_json=json.dumps(slide),
        layout_placeholders='[{"idx": 0, "type": "title"}]',
    )

    assert result.success is True
    assert result.data["valid"] is True
    assert len(result.data["errors"]) == 0


@pytest.mark.asyncio
async def test_validate_invalid_json():
    """Test validation with malformed JSON."""
    result = await ValidateSlideJsonTool.run(
        slide_json="not valid json",
    )

    assert result.success is False
    assert "Invalid JSON" in result.error


@pytest.mark.asyncio
async def test_validate_missing_fields():
    """Test validation with missing required fields."""
    slide = {"slideIndex": 0}

    result = await ValidateSlideJsonTool.run(
        slide_json=json.dumps(slide),
    )

    assert result.success is False
    assert len(result.data["errors"]) > 0


@pytest.mark.asyncio
async def test_validate_invalid_content_type():
    """Test validation with invalid content type."""
    slide = {
        "slideIndex": 0,
        "layoutId": "1",
        "layoutName": "Default",
        "contents": [{"type": "invalid_type"}],
    }

    result = await ValidateSlideJsonTool.run(
        slide_json=json.dumps(slide),
    )

    assert result.success is False


@pytest.mark.asyncio
async def test_validate_text_without_runs():
    """Test validation of text content missing runs array."""
    slide = {
        "slideIndex": 0,
        "layoutId": "1",
        "layoutName": "Default",
        "contents": [{"type": "text", "placeholderIdx": 0}],
    }

    result = await ValidateSlideJsonTool.run(
        slide_json=json.dumps(slide),
    )

    assert result.success is False


@pytest.mark.asyncio
async def test_validate_placeholder_warning():
    """Test validation warns on non-matching placeholder indices."""
    slide = {
        "slideIndex": 0,
        "layoutId": "1",
        "layoutName": "Default",
        "contents": [
            {
                "type": "text",
                "placeholderIdx": 99,
                "runs": [{"text": "Test"}],
            }
        ],
    }

    result = await ValidateSlideJsonTool.run(
        slide_json=json.dumps(slide),
        layout_placeholders='[{"idx": 0, "type": "title"}]',
    )

    # Should still be valid (it's a warning, not an error)
    assert result.success is True
    assert len(result.data["warnings"]) > 0

# ── select_layout ──────────────────────────────────────────────

from app.agent.tools.select_layout import SelectLayoutTool

LAYOUT_CATALOG = [
    {"layoutId": "1", "layoutName": "Title Slide", "masterId": "1",
     "placeholders": [{"idx": 0, "type": "title"}, {"idx": 1, "type": "subtitle"}]},
    {"layoutId": "2", "layoutName": "Title and Content", "masterId": "1",
     "placeholders": [{"idx": 0, "type": "title"}, {"idx": 1, "type": "body"}]},
    {"layoutId": "6", "layoutName": "Conclusion", "masterId": "1",
     "placeholders": [{"idx": 0, "type": "title"}]},
]


@pytest.mark.asyncio
async def test_select_layout_accepts_wrapped_catalog():
    """The design system passes {'layouts': [...]}; the tool must unwrap it.

    Regression: previously it iterated the wrapper dict, hit KeyError(0),
    and returned success=False with error '0' - surfacing as a failed step.
    """
    result = await SelectLayoutTool.run(
        content_type="content",
        available_layouts=json.dumps({"layouts": LAYOUT_CATALOG}),
        slide_index=0,
    )

    assert result.success is True
    assert result.data["layoutId"] == "2"
    assert result.data["layoutName"] == "Title and Content"
    assert result.data["placeholders"][0]["type"] == "title"


@pytest.mark.asyncio
async def test_select_layout_accepts_bare_list():
    """A bare list of layouts is still accepted."""
    result = await SelectLayoutTool.run(
        content_type="title",
        available_layouts=json.dumps(LAYOUT_CATALOG),
        slide_index=0,
    )

    assert result.success is True
    assert result.data["layoutName"] == "Title Slide"


@pytest.mark.asyncio
async def test_select_layout_picks_conclusion_layout():
    """content_type=conclusion selects the Conclusion layout."""
    result = await SelectLayoutTool.run(
        content_type="conclusion",
        available_layouts=json.dumps({"layouts": LAYOUT_CATALOG}),
        slide_index=1,
    )

    assert result.success is True
    assert result.data["layoutName"] == "Conclusion"


# ── design_slide_content / plan_presentation_structure: fail loudly on ────
# unparseable or empty LLM output, instead of silently succeeding with no
# content (previously "right slide count, no content" in the rendered deck).

from app.agent import run_context
from app.agent.tools.design_slide_content import DesignSlideContentTool
from app.agent.tools.plan_presentation_structure import PlanPresentationStructureTool


class _ScriptedProvider:
    def __init__(self, content: str):
        self.content = content

    async def chat(self, messages, tools=None):
        return {"message": {"content": self.content}}


@pytest.mark.asyncio
async def test_design_slide_content_fails_when_response_is_not_json():
    token = run_context.set_chat_provider(_ScriptedProvider("Sorry, I can't do that."))
    try:
        result = await DesignSlideContentTool.run(
            slide_outline="{}", layout_info="{}", slide_index=0,
        )
    finally:
        run_context.reset_chat_provider(token)

    assert result.success is False
    assert "parse" in result.error.lower()


@pytest.mark.asyncio
async def test_design_slide_content_fails_when_contents_is_empty():
    """Regression: valid JSON but with no contents previously still
    succeeded, producing a slide with nothing on it."""
    token = run_context.set_chat_provider(_ScriptedProvider('{"slideIndex": 0, "contents": []}'))
    try:
        result = await DesignSlideContentTool.run(
            slide_outline="{}", layout_info="{}", slide_index=0,
        )
    finally:
        run_context.reset_chat_provider(token)

    assert result.success is False
    assert "no content" in result.error.lower()


@pytest.mark.asyncio
async def test_design_slide_content_recovers_json_wrapped_in_prose():
    content = 'Sure, here you go:\n\n{"slideIndex": 0, "contents": [{"type": "text", "placeholderIdx": 0, "runs": [{"text": "Hi"}]}]}\n\nHope that helps!'
    token = run_context.set_chat_provider(_ScriptedProvider(content))
    try:
        result = await DesignSlideContentTool.run(
            slide_outline="{}", layout_info="{}", slide_index=0,
        )
    finally:
        run_context.reset_chat_provider(token)

    assert result.success is True
    assert result.data["contents"][0]["runs"][0]["text"] == "Hi"


@pytest.mark.asyncio
async def test_plan_presentation_structure_fails_when_response_is_not_json():
    token = run_context.set_chat_provider(_ScriptedProvider("I need more information first."))
    try:
        result = await PlanPresentationStructureTool.run(content_summary="x")
    finally:
        run_context.reset_chat_provider(token)

    assert result.success is False
    assert "parse" in result.error.lower()


@pytest.mark.asyncio
async def test_plan_presentation_structure_fails_when_slides_is_empty():
    token = run_context.set_chat_provider(_ScriptedProvider('{"title": "T", "totalSlides": 0, "slides": []}'))
    try:
        result = await PlanPresentationStructureTool.run(content_summary="x")
    finally:
        run_context.reset_chat_provider(token)

    assert result.success is False


# ── ask_user_question: options as a native list ─────────────────────────
# Regression: reported production crash - "the JSON object must be str,
# bytes or bytearray, not list" - the model sent a real JSON array for
# "options" (matching its now-corrected "array" schema type) rather than a
# JSON-encoded string, and the tool's old bare json.loads(options) raised
# TypeError on it.

from app.agent.tools.ask_user_question import AskUserQuestionTool


@pytest.mark.asyncio
async def test_ask_user_question_accepts_options_as_a_native_list():
    result = await AskUserQuestionTool.run(
        question="What should the presentation focus on?",
        options=["History and Growth", "Menu and Offerings", "Brand Strategy"],
    )

    assert result.success is True
    assert result.data["options"] == ["History and Growth", "Menu and Offerings", "Brand Strategy"]


@pytest.mark.asyncio
async def test_ask_user_question_still_accepts_a_json_encoded_string():
    result = await AskUserQuestionTool.run(
        question="What should the presentation focus on?",
        options='["A", "B", "C"]',
    )

    assert result.success is True
    assert result.data["options"] == ["A", "B", "C"]


@pytest.mark.asyncio
async def test_ask_user_question_treats_a_plain_string_as_one_option():
    result = await AskUserQuestionTool.run(question="Pick one", options="Only choice")

    assert result.success is True
    assert result.data["options"] == ["Only choice"]


# ── registry: array-typed parameters get an items schema ────────────────


def test_ask_user_question_schema_declares_options_as_a_string_array():
    from app.agent.registry import get_registry

    schemas = get_registry().get_function_schemas()
    schema = next(s for s in schemas if s["function"]["name"] == "ask_user_question")
    options_prop = schema["function"]["parameters"]["properties"]["options"]

    assert options_prop["type"] == "array"
    assert options_prop["items"] == {"type": "string"}
