"""
Tool: generate_image_prompt
Creates a detailed prompt for AI image generation that matches the slide's design context.
"""

from __future__ import annotations
from app.agent.registry import ToolRegistry, ToolResult, ToolParameter
from app.agent.run_context import get_chat_provider
from app.llm.provider import get_llm_provider
from app.observability.logging import get_logger
import json
import time

logger = get_logger(__name__)


class GenerateImagePromptTool:
    """Generate a detailed image prompt for AI image generation."""

    @classmethod
    def register(cls, registry: ToolRegistry) -> None:
        registry.register_tool(
            name="generate_image_prompt",
            description=(
                "Generate a detailed prompt for AI image generation that is consistent "
                "with the presentation's design context. The prompt describes the desired "
                "image in detail including style, composition, colors, and mood."
            ),
            fn=cls.run,
            parameters=[
                ToolParameter(
                    name="slide_context",
                    type="string",
                    description="Description of the slide content and what the image should convey",
                ),
                ToolParameter(
                    name="design_palette",
                    type="string",
                    description="JSON string of the color palette from the design system",
                    required=False,
                ),
                ToolParameter(
                    name="image_style",
                    type="string",
                    description="Style preference: photorealistic, illustration, icon, abstract, diagram",
                    required=False,
                    enum=["photorealistic", "illustration", "icon", "abstract", "diagram"],
                ),
                ToolParameter(
                    name="aspect_ratio",
                    type="string",
                    description="Aspect ratio: '16:9', '4:3', '1:1', '3:2'",
                    required=False,
                ),
            ],
        )

    @classmethod
    async def run(
        cls,
        slide_context: str,
        design_palette: str = "{}",
        image_style: str = "illustration",
        aspect_ratio: str = "16:9",
    ) -> ToolResult:
        start = time.time()
        try:
            # The organisation's configured model when a run published one,
            # otherwise the process-wide provider from the environment.
            llm = get_chat_provider() or get_llm_provider()

            try:
                palette = json.loads(design_palette) if design_palette else {}
            except json.JSONDecodeError:
                palette = {}

            accent_colors = []
            if palette.get("colors"):
                accent_colors = [
                    c.get("value", "") for c in palette["colors"]
                    if "accent" in c.get("name", "").lower() and c.get("value", "").startswith("#")
                ][:3]

            color_guidance = ""
            if accent_colors:
                color_guidance = f"\nUse these brand colors in the image: {', '.join(accent_colors)}"

            prompt = f"""Generate a detailed image generation prompt for a presentation slide.

Slide Context: {slide_context}
Image Style: {image_style}
Aspect Ratio: {aspect_ratio}
{color_guidance}

Return a JSON object:
{{
  "prompt": "Detailed image generation prompt (2-3 sentences)",
  "negative_prompt": "Things to avoid in the image",
  "style": "{image_style}",
  "aspect_ratio": "{aspect_ratio}",
  "suggested_position": {{
    "xPercent": 50,
    "yPercent": 30,
    "widthPercent": 40,
    "heightPercent": 50
  }}
}}

The prompt should be detailed, professional, and suitable for the presentation context."""

            response = await llm.chat(
                messages=[{"role": "user", "content": prompt}],
                tools=None,
            )

            result_text = response.get("message", {}).get("content", "{}")
            try:
                image_prompt_data = json.loads(result_text)
            except json.JSONDecodeError:
                import re
                json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', result_text, re.DOTALL)
                if json_match:
                    image_prompt_data = json.loads(json_match.group(1))
                else:
                    image_prompt_data = {
                        "prompt": result_text,
                        "negative_prompt": "",
                        "style": image_style,
                        "aspect_ratio": aspect_ratio,
                    }

            duration_ms = int((time.time() - start) * 1000)
            return ToolResult(
                success=True,
                data=image_prompt_data,
                duration_ms=duration_ms,
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e), duration_ms=int((time.time() - start) * 1000))