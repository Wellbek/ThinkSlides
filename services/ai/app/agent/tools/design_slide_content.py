"""
Tool: design_slide_content
Generates the structured JSON content for a single slide, following the slide JSON schema.
The renderer will consume this to clone layout XML and fill placeholders.
"""

from __future__ import annotations
from app.agent.registry import ToolRegistry, ToolResult, ToolParameter
from app.llm.provider import get_llm_provider
from app.observability.logging import get_logger
import json
import time

logger = get_logger(__name__)


class DesignSlideContentTool:
    """Generate structured slide content JSON for a single slide."""

    @classmethod
    def register(cls, registry: ToolRegistry) -> None:
        registry.register_tool(
            name="design_slide_content",
            description=(
                "Generate the structured JSON content for a single slide. "
                "Takes the slide outline, selected layout, and design system context "
                "to produce content that fills the layout placeholders. "
                "Returns a slide JSON object conforming to the Slide schema."
            ),
            fn=cls.run,
            parameters=[
                ToolParameter(
                    name="slide_outline",
                    type="string",
                    description="JSON string with slide title, keyPoints, contentType, and notes",
                ),
                ToolParameter(
                    name="layout_info",
                    type="string",
                    description="JSON string of the selected layout with placeholder positions",
                ),
                ToolParameter(
                    name="design_system_context",
                    type="string",
                    description="JSON string of the design system (palette, typography, spacing)",
                    required=False,
                ),
                ToolParameter(
                    name="slide_index",
                    type="integer",
                    description="0-based index of this slide in the presentation",
                ),
            ],
        )

    @classmethod
    async def run(
        cls,
        slide_outline: str,
        layout_info: str,
        slide_index: int,
        design_system_context: str = "{}",
    ) -> ToolResult:
        start = time.time()
        try:
            llm = get_llm_provider()

            outline = json.loads(slide_outline) if slide_outline else {}
            layout = json.loads(layout_info) if layout_info else {}
            ds = json.loads(design_system_context) if design_system_context else {}

            placeholders = layout.get("placeholders", [])
            layout_name = layout.get("layoutName", "Default")

            # Build placeholder description for the prompt
            ph_desc = []
            for ph in placeholders:
                ph_desc.append(
                    f"  - idx={ph.get('idx')}, type={ph.get('type')}, name={ph.get('name')}"
                )
            ph_text = "\n".join(ph_desc) if ph_desc else "  No placeholders found"

            ds_info = ""
            if ds:
                ds_info = f"""
Design System Context:
- Title font: {json.dumps(ds.get('typography', {}).get('titleFont', {}))}
- Body font: {json.dumps(ds.get('typography', {}).get('bodyFont', {}))}
- Palette: {json.dumps(ds.get('palette', {}).get('colors', [])[:5])}
"""

            prompt = f"""Generate content for a single presentation slide.

Slide Outline:
{json.dumps(outline, indent=2)}

Selected Layout: {layout_name}
Available Placeholders:
{ph_text}
{ds_info}

Return a JSON object with this exact structure:
{{
  "slideIndex": {slide_index},
  "layoutId": "{layout.get('layoutId', '1')}",
  "layoutName": "{layout_name}",
  "contents": [
    {{
      "type": "text",
      "placeholderIdx": <matching placeholder idx>,
      "runs": [
        {{"text": "Content text", "bold": false, "bullet": false, "level": 0}}
      ]
    }}
  ],
  "notes": "Speaker notes for this slide"
}}

Rules:
- Each content item must have a placeholderIdx matching one of the available placeholders
- Use "text" type for title and body placeholders
- Use "image" type for pic placeholders with {{type: "image", image: {{source: "prompt", data: "description"}}}}
- Use "table" type for tbl placeholders
- For bullet points, set bullet: true and increment level for sub-items
- Keep text concise — slides should not be text-heavy
- Use the design system fonts and colors when specifying style overrides"""

            response = await llm.chat(
                messages=[{"role": "user", "content": prompt}],
                tools=None,
            )

            result_text = response.get("message", {}).get("content", "{}")
            try:
                slide_data = json.loads(result_text)
            except json.JSONDecodeError:
                import re
                json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', result_text, re.DOTALL)
                if json_match:
                    slide_data = json.loads(json_match.group(1))
                else:
                    slide_data = {"raw_content": result_text, "slideIndex": slide_index}

            # Ensure required fields
            slide_data.setdefault("slideIndex", slide_index)
            slide_data.setdefault("layoutId", layout.get("layoutId", "1"))
            slide_data.setdefault("layoutName", layout_name)
            slide_data.setdefault("contents", [])

            duration_ms = int((time.time() - start) * 1000)
            logger.info(
                "slide_content_designed",
                slide_index=slide_index,
                content_count=len(slide_data.get("contents", [])),
                duration_ms=duration_ms,
            )

            return ToolResult(
                success=True,
                data=slide_data,
                duration_ms=duration_ms,
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e), duration_ms=int((time.time() - start) * 1000))