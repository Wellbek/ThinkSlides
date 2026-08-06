"""
Tool: plan_presentation_structure
Plans the overall slide structure and outline based on analyzed content and requirements.
"""

from __future__ import annotations
from app.agent.registry import ToolRegistry, ToolResult, ToolParameter
from app.agent.run_context import get_chat_provider
from app.llm.provider import get_tools_llm_provider as get_llm_provider
from app.observability.logging import get_logger
import json
import time

logger = get_logger(__name__)


class PlanPresentationStructureTool:
    """Plan the slide structure and outline for a presentation."""

    @classmethod
    def register(cls, registry: ToolRegistry) -> None:
        registry.register_tool(
            name="plan_presentation_structure",
            description=(
                "Plan the overall structure and outline of a presentation. "
                "Given content analysis results and requirements, determines the number of slides, "
                "their titles, key points, and suggested layout types. "
                "Returns a structured outline with slide metadata."
            ),
            fn=cls.run,
            parameters=[
                ToolParameter(
                    name="content_summary",
                    type="string",
                    description="Summary of the content to be presented",
                ),
                ToolParameter(
                    name="key_data",
                    type="string",
                    description="JSON string of extracted key data points",
                    required=False,
                ),
                ToolParameter(
                    name="target_slide_count",
                    type="integer",
                    description="Desired number of slides (0 for auto-determine)",
                    required=False,
                ),
                ToolParameter(
                    name="audience",
                    type="string",
                    description="Target audience description",
                    required=False,
                ),
                ToolParameter(
                    name="design_system_summary",
                    type="string",
                    description="JSON summary of the design system layout catalog",
                    required=False,
                ),
            ],
        )

    @classmethod
    async def run(
        cls,
        content_summary: str,
        key_data: str = "{}",
        target_slide_count: int = 0,
        audience: str = "general",
        design_system_summary: str = "{}",
    ) -> ToolResult:
        start = time.time()
        try:
            # An organisation that configured its own provider gets it here
            # too: its explicit choice outranks the environment's cheaper
            # tools-tier model, which only exists for the default provider.
            llm = get_chat_provider() or get_llm_provider()

            try:
                parsed_data = json.loads(key_data) if key_data else {}
            except json.JSONDecodeError:
                parsed_data = {}

            try:
                parsed_ds = json.loads(design_system_summary) if design_system_summary else {}
            except json.JSONDecodeError:
                parsed_ds = {}

            available_layouts = parsed_ds.get("layouts", [])
            layout_info = ""
            if available_layouts:
                layout_names = [lt.get("layoutName", f"Layout {lt.get('layoutId', '?')}") for lt in available_layouts]
                layout_info = f"\nAvailable layout names in template: {', '.join(layout_names)}"

            prompt = f"""Plan a presentation structure based on the following:

Content Summary:
{content_summary[:6000]}

Key Data: {json.dumps(parsed_data, indent=2)[:2000]}

Target Audience: {audience}
Target Slide Count: {target_slide_count if target_slide_count > 0 else 'auto-determine'}
{layout_info}

Return a JSON object with this structure:
{{
  "title": "Presentation Title",
  "totalSlides": <number>,
  "slides": [
    {{
      "slideIndex": 0,
      "title": "Slide Title",
      "keyPoints": ["point1", "point2"],
      "suggestedLayout": "Title Slide",
      "contentType": "title|content|twoColumn|image|table|chart|conclusion",
      "notes": "Any additional notes for content generation"
    }}
  ]
}}

Rules:
- First slide should be a title slide (contentType "title") - it carries only a title and an optional subtitle, no body bullets, so give it 0-1 key points.
- Last slide should be a conclusion/summary slide. If it recaps points (3-5 key points), give it contentType "content" so the points render in a body placeholder - only use contentType "conclusion" (title-only) for a slide with no body content.
- Every slide that has key points MUST use a contentType whose layout has a body placeholder: "content" (one body), "twoColumn" (two bodies), "table", or "image". Never assign 3-5 key points to a "title" or "conclusion" slide - those layouts have no body placeholder and the points would be dropped.
- Distribute content evenly across slides
- Each content slide should have 3-5 key points maximum
- Choose suggestedLayout names from the available layouts if a template is provided"""

            response = await llm.chat(
                messages=[{"role": "user", "content": prompt}],
                tools=None,
            )

            result_text = response.get("message", {}).get("content", "{}")
            try:
                structure = json.loads(result_text)
            except json.JSONDecodeError:
                # Try to extract JSON from markdown code block
                import re
                json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', result_text, re.DOTALL)
                if json_match:
                    structure = json.loads(json_match.group(1))
                else:
                    structure = {"raw_plan": result_text}

            duration_ms = int((time.time() - start) * 1000)
            logger.info(
                "presentation_structure_planned",
                slide_count=structure.get("totalSlides", 0),
                duration_ms=duration_ms,
            )

            return ToolResult(
                success=True,
                data=structure,
                duration_ms=duration_ms,
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e), duration_ms=int((time.time() - start) * 1000))