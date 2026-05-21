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