"""
Tool: select_layout
Chooses the best slide layout from the template's layout catalog based on content type.
"""

from __future__ import annotations
from app.agent.registry import ToolRegistry, ToolResult, ToolParameter
from app.observability.logging import get_logger
import time

logger = get_logger(__name__)

# Mapping from content types to layout name keywords
LAYOUT_KEYWORDS = {
    "title": ["title", "title slide", "cover"],
    "content": ["title and content", "content", "1-content", "title/body"],
    "twoColumn": ["two column", "2-content", "comparison", "two text"],
    "image": ["picture", "picture with caption", "image", "photo"],
    "table": ["table", "data"],
    "chart": ["chart", "graph"],
    "conclusion": ["blank", "section header", "conclusion"],
}


class SelectLayoutTool:
    """Select the best layout from the template for a given slide content type."""

    @classmethod
    def register(cls, registry: ToolRegistry) -> None:
        registry.register_tool(
            name="select_layout",
            description=(
                "Select the best slide layout from the template's layout catalog "
                "based on the content type and available placeholders. "
                "Returns the layout ID, name, and placeholder map."
            ),
            fn=cls.run,
            parameters=[
                ToolParameter(
                    name="content_type",
                    type="string",
                    description="Type of content: title, content, twoColumn, image, table, chart, conclusion",
                    enum=["title", "content", "twoColumn", "image", "table", "chart", "conclusion"],
                ),
                ToolParameter(
                    name="available_layouts",
                    type="string",
                    description="JSON string of the layout catalog from the design system",
                ),
                ToolParameter(
                    name="slide_index",
                    type="integer",
                    description="The slide index (0-based) for this layout selection",
                ),
            ],
        )

    @classmethod
    async def run(
        cls,
        content_type: str,
        available_layouts: str,
        slide_index: int,
    ) -> ToolResult:
        start = time.time()
        try:
            import json
            parsed = json.loads(available_layouts) if available_layouts else []
            # The design system passes the layout catalog wrapped as
            # ``{"layouts": [...]}``. Accept that wrapper as well as a bare
            # list so the tool never iterates a dict by mistake (which
            # previously raised KeyError and surfaced as a failed step).
            if isinstance(parsed, dict):
                layouts = parsed.get("layouts", [])
            elif isinstance(parsed, list):
                layouts = parsed
            else:
                layouts = []

            if not layouts:
                return ToolResult(
                    success=True,
                    data={
                        "layoutId": "1",
                        "layoutName": "Default",
                        "masterId": "1",
                        "placeholders": [],
                        "selectionReason": "No layouts provided, using default",
                    },
                    duration_ms=int((time.time() - start) * 1000),
                )

            # Score each layout based on content type match
            keywords = LAYOUT_KEYWORDS.get(content_type, ["blank"])
            best_layout = layouts[0]
            best_score = -1

            for layout in layouts:
                name_lower = layout.get("layoutName", "").lower()
                score = 0

                # Keyword matching
                for keyword in keywords:
                    if keyword in name_lower:
                        score += 10
                        break

                # Placeholder type matching
                placeholders = layout.get("placeholders", [])
                ph_types = [p.get("type", "") for p in placeholders]

                if content_type == "title":
                    if "ctrTitle" in ph_types or "title" in ph_types:
                        score += 5
                    if "subtitle" in ph_types:
                        score += 3

                elif content_type == "content":
                    if "title" in ph_types:
                        score += 3
                    if "body" in ph_types:
                        score += 5

                elif content_type == "twoColumn":
                    body_count = ph_types.count("body")
                    if body_count >= 2:
                        score += 8
                    elif body_count >= 1:
                        score += 3

                elif content_type == "image":
                    if "pic" in ph_types:
                        score += 8
                    if "title" in ph_types:
                        score += 2

                elif content_type == "table":
                    if "tbl" in ph_types or "body" in ph_types:
                        score += 5

                elif content_type == "chart":
                    if "chart" in ph_types:
                        score += 8
                    if "body" in ph_types:
                        score += 3

                elif content_type == "conclusion":
                    if "title" in ph_types:
                        score += 3
                    if "body" in ph_types:
                        score += 2
                    # Prefer simpler layouts for conclusion
                    score += max(0, 5 - len(placeholders))

                if score > best_score:
                    best_score = score
                    best_layout = layout

            duration_ms = int((time.time() - start) * 1000)
            return ToolResult(
                success=True,
                data={
                    "layoutId": best_layout.get("layoutId", "1"),
                    "layoutName": best_layout.get("layoutName", "Default"),
                    "masterId": best_layout.get("masterId", "1"),
                    "placeholders": best_layout.get("placeholders", []),
                    "selectionReason": f"Matched content_type='{content_type}' with score {best_score}",
                },
                duration_ms=duration_ms,
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e), duration_ms=int((time.time() - start) * 1000))