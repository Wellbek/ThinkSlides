"""
Tool: select_source_image

Confidence-gated selection of an already-existing image (extracted from the
user's uploaded documents, or found via web_search) for a slide - never
generates a new image. Replaces the old generate_image_prompt tool, which
only ever produced a text description nothing downstream consumed.

Receives lightweight catalog metadata only (id + descriptive context, never
the raw image bytes/URL) so the LLM's decision never has to carry image
payloads through its own output. The agent loop resolves the chosen id back
to the real image data server-side (see loop.py's tool-dispatch handling)
and folds it into the corresponding slide's content.
"""

from __future__ import annotations
from app.agent.registry import ToolRegistry, ToolResult, ToolParameter
from app.agent.run_context import require_chat_provider
from app.agent.tools._json_extract import coerce_json_arg, extract_json_object
from app.observability.logging import get_logger
import time

logger = get_logger(__name__)


class SelectSourceImageTool:
    """Pick a confidently-relevant catalog image for a slide, or none."""

    @classmethod
    def register(cls, registry: ToolRegistry) -> None:
        registry.register_tool(
            name="select_source_image",
            description=(
                "Check whether any already-extracted source image (from the user's uploaded "
                "documents or from web_search results) is a confident, specific match for a "
                "slide, and select it if so. This NEVER generates a new image - it only reuses "
                "images that genuinely already exist. Only call this when the image catalog is "
                "non-empty and the slide's topic plausibly has a relevant source image; skip it "
                "entirely for slides where that's not the case. Defaults to selecting nothing "
                "unless the match is clearly, specifically relevant - a generically related "
                "image is worse than no image."
            ),
            fn=cls.run,
            parameters=[
                ToolParameter(
                    name="slide_index",
                    type="integer",
                    description="Index of the slide this selection is for",
                ),
                ToolParameter(
                    name="slide_topic",
                    type="string",
                    description="Brief description of what this specific slide covers, for matching against the catalog",
                ),
                ToolParameter(
                    name="catalog_json",
                    type="string",
                    description="JSON array of candidate images (id + context only). Auto-supplied by the agent loop.",
                    required=False,
                ),
            ],
        )

    @classmethod
    async def run(
        cls,
        slide_index: int,
        slide_topic: str,
        catalog_json: str = "[]",
    ) -> ToolResult:
        start = time.time()
        try:
            catalog = coerce_json_arg(catalog_json, [])
            if not isinstance(catalog, list) or not catalog:
                return ToolResult(
                    success=True,
                    data={"slideIndex": slide_index, "selected": False, "imageId": None,
                          "reason": "No candidate images available for this presentation."},
                    duration_ms=int((time.time() - start) * 1000),
                )

            catalog_lines = "\n".join(
                f"- id: {entry.get('id')} | context: {(entry.get('context') or '')[:220]}"
                for entry in catalog if isinstance(entry, dict) and entry.get("id")
            )

            prompt = f"""A slide in a presentation covers: "{slide_topic}"

Here are images already available (extracted from the user's own uploaded documents, or found via web search) - NOT images to be generated:
{catalog_lines}

Decide whether ANY of these images is a clearly, specifically relevant match for this exact slide - not just generically on-topic. Err strongly toward selecting nothing: a wrong or loosely-related image actively hurts the presentation, while no image is always safe.

Return ONLY a JSON object:
{{"selected": true|false, "imageId": "<id from the list above, or null>", "reason": "one sentence explaining the decision"}}"""

            llm = require_chat_provider()
            response = await llm.chat(messages=[{"role": "user", "content": prompt}], tools=None)
            result_text = response.get("message", {}).get("content", "{}")
            parsed = extract_json_object(result_text)

            if parsed is None:
                return ToolResult(
                    success=True,
                    data={"slideIndex": slide_index, "selected": False, "imageId": None,
                          "reason": "Could not parse a selection decision; defaulting to no image."},
                    duration_ms=int((time.time() - start) * 1000),
                )

            valid_ids = {entry.get("id") for entry in catalog if isinstance(entry, dict)}
            image_id = parsed.get("imageId")
            selected = bool(parsed.get("selected")) and image_id in valid_ids

            duration_ms = int((time.time() - start) * 1000)
            logger.info(
                "source_image_selection",
                slide_index=slide_index,
                selected=selected,
                image_id=image_id if selected else None,
            )
            return ToolResult(
                success=True,
                data={
                    "slideIndex": slide_index,
                    "selected": selected,
                    "imageId": image_id if selected else None,
                    "reason": parsed.get("reason", ""),
                },
                duration_ms=duration_ms,
            )
        except Exception as e:
            logger.error("select_source_image_failed", error=str(e))
            return ToolResult(success=False, error=str(e), duration_ms=int((time.time() - start) * 1000))
