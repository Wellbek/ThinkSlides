"""
Tool: validate_slide_json
Validates a slide JSON object against the shared slide schema before rendering.
Ensures all required fields exist and placeholder indices are valid.
"""

from __future__ import annotations
from app.agent.registry import ToolRegistry, ToolResult, ToolParameter
from app.observability.logging import get_logger
import json
import time

logger = get_logger(__name__)

REQUIRED_SLIDE_FIELDS = ["slideIndex", "layoutId", "layoutName", "contents"]
REQUIRED_CONTENT_FIELDS = ["type"]
VALID_CONTENT_TYPES = {"text", "image", "table", "chart"}


class ValidateSlideJsonTool:
    """Validate slide JSON against the schema before rendering."""

    @classmethod
    def register(cls, registry: ToolRegistry) -> None:
        registry.register_tool(
            name="validate_slide_json",
            description=(
                "Validate a slide JSON object against the shared schema. "
                "Checks required fields, content types, placeholder indices, "
                "and data integrity. Returns validation result with any errors or warnings."
            ),
            fn=cls.run,
            parameters=[
                ToolParameter(
                    name="slide_json",
                    type="string",
                    description="JSON string of the slide to validate",
                ),
                ToolParameter(
                    name="layout_placeholders",
                    type="string",
                    description="JSON string of available layout placeholder indices and types",
                    required=False,
                ),
            ],
        )

    @classmethod
    async def run(
        cls,
        slide_json: str,
        layout_placeholders: str = "[]",
    ) -> ToolResult:
        start = time.time()
        try:
            slide = json.loads(slide_json)
        except json.JSONDecodeError as e:
            return ToolResult(
                success=False,
                error=f"Invalid JSON: {str(e)}",
                duration_ms=int((time.time() - start) * 1000),
            )

        errors: list[str] = []
        warnings: list[str] = []

        # Check required top-level fields
        for field in REQUIRED_SLIDE_FIELDS:
            if field not in slide:
                errors.append(f"Missing required field: {field}")

        if errors:
            return ToolResult(
                success=False,
                error="; ".join(errors),
                data={"errors": errors, "warnings": warnings},
                duration_ms=int((time.time() - start) * 1000),
            )

        # Validate contents array
        contents = slide.get("contents", [])
        if not isinstance(contents, list):
            errors.append("'contents' must be an array")
        else:
            for i, item in enumerate(contents):
                # Check required content fields
                for field in REQUIRED_CONTENT_FIELDS:
                    if field not in item:
                        errors.append(f"Content item {i}: missing '{field}'")

                # Check content type
                ctype = item.get("type", "")
                if ctype not in VALID_CONTENT_TYPES:
                    errors.append(
                        f"Content item {i}: invalid type '{ctype}', must be one of {VALID_CONTENT_TYPES}"
                    )

                # Type-specific validation
                if ctype == "text":
                    if "runs" not in item:
                        errors.append(f"Content item {i}: text type requires 'runs' array")
                    elif not isinstance(item.get("runs"), list):
                        errors.append(f"Content item {i}: 'runs' must be an array")
                    else:
                        for j, run in enumerate(item["runs"]):
                            if "text" not in run:
                                errors.append(f"Content item {i}, run {j}: missing 'text' field")

                elif ctype == "image":
                    if "image" not in item:
                        errors.append(f"Content item {i}: image type requires 'image' object")

                elif ctype == "table":
                    if "table" not in item:
                        errors.append(f"Content item {i}: table type requires 'table' object")
                    else:
                        tbl = item["table"]
                        if "rows" not in tbl:
                            errors.append(f"Content item {i}: table requires 'rows' array")

                elif ctype == "chart":
                    if "chart" not in item:
                        errors.append(f"Content item {i}: chart type requires 'chart' object")

                # Validate placeholder index against layout
                ph_idx = item.get("placeholderIdx")
                if ph_idx is not None and layout_placeholders:
                    try:
                        valid_phs = json.loads(layout_placeholders)
                        valid_indices = [p.get("idx") for p in valid_phs]
                        if ph_idx not in valid_indices:
                            warnings.append(
                                f"Content item {i}: placeholderIdx {ph_idx} not in layout placeholders {valid_indices}"
                            )
                    except json.JSONDecodeError:
                        pass

        # Validate slide index
        if not isinstance(slide.get("slideIndex"), int):
            errors.append("slideIndex must be an integer")

        if slide.get("slideIndex", -1) < 0:
            warnings.append("slideIndex should be >= 0")

        # Validate layoutId
        if not slide.get("layoutId"):
            warnings.append("layoutId is empty — renderer may use default layout")

        duration_ms = int((time.time() - start) * 1000)
        is_valid = len(errors) == 0

        return ToolResult(
            success=is_valid,
            data={
                "valid": is_valid,
                "errors": errors,
                "warnings": warnings,
                "slideIndex": slide.get("slideIndex"),
                "contentCount": len(contents),
            },
            error="; ".join(errors) if errors else None,
            duration_ms=duration_ms,
        )