"""
Tool: validate_slide_json
Validates a slide JSON object against the shared slide schema before rendering.
Ensures all required fields exist and placeholder indices are valid.
"""

from __future__ import annotations
from app.agent.registry import ToolRegistry, ToolResult, ToolParameter
from app.agent.tools._json_extract import coerce_json_arg
from app.observability.logging import get_logger
from app.renderer import regions as region_model
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
        # coerce_json_arg, not a bare json.loads: a tool-calling model may
        # send a native JSON value here despite the declared "string" type,
        # which raises TypeError - a plain `except json.JSONDecodeError`
        # does not catch that and would crash the whole agent loop.
        slide = coerce_json_arg(slide_json, None)
        if slide is None:
            return ToolResult(
                success=False,
                error=f"Invalid JSON: could not parse slide_json (got: {str(slide_json)[:200]!r})",
                duration_ms=int((time.time() - start) * 1000),
            )

        # Guard against non-object payloads. The LLM occasionally passes a
        # JSON string, a bare string, or an array of slides here; crashing on
        # ``slide.get(...)`` derails the whole agent loop.
        if isinstance(slide, list):
            return ToolResult(
                success=False,
                error=(
                    "validate_slide_json expects a single slide object, but received an array. "
                    "Call it once per slide with one slide object as slide_json."
                ),
                data={"errors": ["expected a slide object, got an array"]},
                duration_ms=int((time.time() - start) * 1000),
            )
        if not isinstance(slide, dict):
            return ToolResult(
                success=False,
                error=(
                    f"validate_slide_json expects a slide object (dict), but got "
                    f"{type(slide).__name__}. Pass a single slide object as slide_json."
                ),
                data={"errors": [f"expected object, got {type(slide).__name__}"]},
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
                # Each content item must be an object; the LLM occasionally
                # emits a bare string here, which would crash the .get calls
                # below.
                if not isinstance(item, dict):
                    errors.append(
                        f"Content item {i}: must be an object, got {type(item).__name__}"
                    )
                    continue

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
                    elif not isinstance(item["table"], dict):
                        errors.append(f"Content item {i}: 'table' must be an object")
                    else:
                        tbl = item["table"]
                        rows = tbl.get("rows")
                        if "rows" not in tbl:
                            errors.append(f"Content item {i}: table requires 'rows' array")
                        elif not isinstance(rows, list) or not rows:
                            errors.append(f"Content item {i}: table 'rows' must be a non-empty array")
                        elif not all(isinstance(r, list) for r in rows):
                            errors.append(f"Content item {i}: every table row must be an array of cell values")
                        else:
                            col_counts = {len(r) for r in rows}
                            if max(col_counts, default=0) == 0:
                                errors.append(f"Content item {i}: table rows are all empty")
                            elif len(col_counts) > 1:
                                warnings.append(
                                    f"Content item {i}: table rows have inconsistent column counts "
                                    f"{sorted(col_counts)} - shorter rows render with blank trailing cells"
                                )

                elif ctype == "chart":
                    if "chart" not in item:
                        errors.append(f"Content item {i}: chart type requires 'chart' object")

                # Validate placeholder index against layout
                ph_idx = item.get("placeholderIdx")
                if ph_idx is not None and layout_placeholders:
                    valid_phs = coerce_json_arg(layout_placeholders, None)
                    if isinstance(valid_phs, list):
                        valid_indices = [p.get("idx") for p in valid_phs]
                        if ph_idx not in valid_indices:
                            warnings.append(
                                f"Content item {i}: placeholderIdx {ph_idx} not in layout placeholders {valid_indices}"
                            )

        # A composed slide's regions have to be checked before render, not
        # discovered in the PPTX: overlapping siblings draw content over
        # content, a region escaping its parent puts content outside the area
        # it was composed into, and a block bound to a region that does not
        # exist is content silently dropped. All three produce a file that
        # opens and is wrong, which is the worst kind of defect to ship.
        region_errors, region_warnings = region_model.validate_regions(
            slide.get("regions"),
            region_model.content_region_ids(contents),
        )
        errors.extend(region_errors)
        warnings.extend(region_warnings)

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