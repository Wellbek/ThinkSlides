"""
Tests for agent tool implementations.
"""

import json

import pytest

from app.agent import run_context
from app.agent.tools.ask_user_question import AskUserQuestionTool
from app.agent.tools.design_slide_content import DesignSlideContentTool
from app.agent.tools.plan_presentation_structure import PlanPresentationStructureTool
from app.agent.tools.select_layout import SelectLayoutTool
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


@pytest.mark.asyncio
async def test_validate_valid_table():
    slide = {
        "slideIndex": 0,
        "layoutId": "1",
        "layoutName": "Default",
        "contents": [{
            "type": "table",
            "table": {"rows": [["Region", "Revenue"], ["EMEA", "$4.2M"], ["APAC", "$3.1M"]], "headerRow": True},
        }],
    }
    result = await ValidateSlideJsonTool.run(slide_json=json.dumps(slide))
    assert result.success is True
    assert result.data["errors"] == []


@pytest.mark.asyncio
async def test_validate_table_missing_rows():
    slide = {
        "slideIndex": 0,
        "layoutId": "1",
        "layoutName": "Default",
        "contents": [{"type": "table", "table": {}}],
    }
    result = await ValidateSlideJsonTool.run(slide_json=json.dumps(slide))
    assert result.success is False
    assert any("rows" in e for e in result.data["errors"])


@pytest.mark.asyncio
async def test_validate_table_rows_not_arrays():
    slide = {
        "slideIndex": 0,
        "layoutId": "1",
        "layoutName": "Default",
        "contents": [{"type": "table", "table": {"rows": ["not", "a", "grid"]}}],
    }
    result = await ValidateSlideJsonTool.run(slide_json=json.dumps(slide))
    assert result.success is False
    assert any("array of cell values" in e for e in result.data["errors"])


@pytest.mark.asyncio
async def test_validate_table_ragged_rows_warns_not_errors():
    slide = {
        "slideIndex": 0,
        "layoutId": "1",
        "layoutName": "Default",
        "contents": [{"type": "table", "table": {"rows": [["A", "B"], ["only-one"]]}}],
    }
    result = await ValidateSlideJsonTool.run(slide_json=json.dumps(slide))
    assert result.success is True
    assert any("inconsistent column counts" in w for w in result.data["warnings"])


# ── select_layout ──────────────────────────────────────────────


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
async def test_design_slide_content_rejects_an_inline_chart():
    """The prompt has always said to call build_chart instead of writing
    chart JSON here - as prose the model could, and did, ignore. This is
    that instruction enforced: a "chart" content item is refused outright,
    with build_chart's real chart-type guidance and validation named as the
    only path, rather than quietly rendering through the 4-type shape below
    it."""
    content = json.dumps({
        "slideIndex": 0,
        "contents": [
            {"type": "text", "placeholderIdx": 0, "runs": [{"text": "Revenue"}]},
            {"type": "chart", "placeholderIdx": 1, "chart": {
                "chartType": "bar", "categories": ["2024"], "series": [{"name": "R", "values": [1]}],
            }},
        ],
    })
    token = run_context.set_chat_provider(_ScriptedProvider(content))
    try:
        result = await DesignSlideContentTool.run(
            slide_outline="{}", layout_info="{}", slide_index=0,
        )
    finally:
        run_context.reset_chat_provider(token)

    assert result.success is False
    assert "build_chart" in result.error


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
async def test_ask_user_question_rejects_a_question_with_nothing_to_pick_from():
    """A question with one suggestion (or none) leaves the user with nothing to
    react to, which is the failure mode the options exist to prevent - so it is
    sent back for the model to ask properly rather than shown as-is."""
    result = await AskUserQuestionTool.run(question="Pick one", options="Only choice")

    assert result.success is False
    assert "Pick one" in result.error
    assert "2" in result.error


@pytest.mark.asyncio
async def test_ask_user_question_asks_several_things_in_one_pause():
    result = await AskUserQuestionTool.run(
        questions=[
            {"question": "Who is it for?", "options": ["Investors", "Staff"]},
            {"question": "How long?", "options": ["5 slides", "10 slides", "15 slides"]},
        ],
        context="Planning a deck about coffee",
    )

    assert result.success is True
    assert [q["question"] for q in result.data["questions"]] == [
        "Who is it for?",
        "How long?",
    ]
    # The first question also fills the single-question fields, which older
    # persisted sessions and the chat-message rendering still read.
    assert result.data["question"] == "Who is it for?"
    assert result.data["options"] == ["Investors", "Staff"]
    assert result.data["context"] == "Planning a deck about coffee"


@pytest.mark.asyncio
async def test_ask_user_question_accepts_the_questions_array_as_a_json_string():
    result = await AskUserQuestionTool.run(
        questions='[{"question": "Which angle?", "options": ["History", "Growth"]}]'
    )

    assert result.success is True
    assert result.data["questions"][0]["options"] == ["History", "Growth"]


@pytest.mark.asyncio
async def test_ask_user_question_collapses_duplicate_options():
    result = await AskUserQuestionTool.run(
        question="Which colour?", options=["Green", "green", "Blue"]
    )

    assert result.success is True
    assert result.data["options"] == ["Green", "Blue"]


# ── registry: array-typed parameters get an items schema ────────────────


def test_ask_user_question_schema_declares_options_as_a_string_array():
    from app.agent.registry import get_registry

    schemas = get_registry().get_function_schemas()
    schema = next(s for s in schemas if s["function"]["name"] == "ask_user_question")
    options_prop = schema["function"]["parameters"]["properties"]["options"]

    assert options_prop["type"] == "array"
    assert options_prop["items"] == {"type": "string"}


@pytest.mark.asyncio
async def test_ask_user_question_hoists_context_attached_to_a_question():
    """Observed on the outline review: the model put the outline on the
    question object instead of the call, so the user was asked to approve a
    plan they could not see."""
    result = await AskUserQuestionTool.run(
        questions=[{
            "question": "Shall we go ahead with this outline?",
            "options": ["Go ahead as planned", "Add a slide on risks"],
            "context": "1. Cover\n2. Market size\n3. Outlook",
        }],
    )

    assert result.success is True
    assert result.data["context"] == "1. Cover\n2. Market size\n3. Outlook"


@pytest.mark.asyncio
async def test_an_explicit_call_context_wins_over_a_hoisted_one():
    result = await AskUserQuestionTool.run(
        questions=[{
            "question": "Shall we go ahead?",
            "options": ["Yes", "Add a slide on risks"],
            "context": "inner",
        }],
        context="outer",
    )

    assert result.data["context"] == "outer"
