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
