"""
Tool: design_slide_content
Generates the structured JSON content for a single slide, following the slide JSON schema.
The renderer will consume this to clone layout XML and fill placeholders.
"""

from __future__ import annotations
from app.agent.registry import ToolRegistry, ToolResult, ToolParameter
from app.agent.run_context import get_chat_provider
from app.agent.tools._json_extract import coerce_json_arg, extract_json_object
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
                    description=(
                        "JSON string of the selected layout with placeholder positions. "
                        "Optional - the system supplies the layout you selected via "
                        "select_layout for this slide_index automatically."
                    ),
                    required=False,
                ),
                ToolParameter(
                    name="design_system_context",
                    type="string",
                    description=(
                        "JSON string of the design system (palette, typography, spacing). "
                        "Optional - the system supplies the current design system "
                        "automatically, so you do not need to pass this."
                    ),
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
        slide_index: int,
        layout_info: str = "{}",
        design_system_context: str = "{}",
    ) -> ToolResult:
        start = time.time()
        try:
            # The organisation's configured model when a run published one,
            # otherwise the process-wide provider from the environment. This
            # tool writes the actual slide copy, so it is the call that most
            # needs to honour the model chosen in Settings.
            llm = get_chat_provider() or get_llm_provider()

            # coerce_json_arg (not a bare json.loads) because tool-calling
            # models frequently send a native JSON object here despite the
            # parameter's declared "string" type, which crashes json.loads
            # with a TypeError rather than the JSONDecodeError this used to
            # (harmlessly) propagate as a tool failure. A genuinely
            # unparseable slide_outline must still fail the call rather than
            # silently proceeding with no key points - that's the same
            # empty-slide failure mode already fixed for the model's overall
            # response below, just one level up in the argument itself.
            outline = coerce_json_arg(slide_outline, None)
            if outline is None:
                return ToolResult(
                    success=False,
                    error=(
                        "Could not parse slide_outline as JSON "
                        f"(got: {str(slide_outline)[:200]!r}). Retry with a valid JSON object."
                    ),
                    duration_ms=int((time.time() - start) * 1000),
                )
            layout = coerce_json_arg(layout_info, {})
            ds = coerce_json_arg(design_system_context, {})

            placeholders = layout.get("placeholders", [])
            layout_name = layout.get("layoutName", "Default")

            # Resolve the title and body placeholder indices so the LLM does
            # not have to guess. Falls back to conventional idx 0 (title) and
            # 1 (body) when the layout did not report placeholders - this
            # keeps content placeable even if layout selection upstream was
            # imperfect, instead of producing an empty slide.
            title_idx = next(
                (p.get("idx") for p in placeholders
                 if p.get("type") in ("title", "ctrTitle")),
                0,
            )
            body_idx = next(
                (p.get("idx") for p in placeholders if p.get("type") == "body"),
                1,
            )

            # Build placeholder description for the prompt
            ph_desc = []
            for ph in placeholders:
                ph_desc.append(
                    f"  - idx={ph.get('idx')}, type={ph.get('type')}, name={ph.get('name')}"
                )
            ph_text = "\n".join(ph_desc) if ph_desc else "  No placeholders reported (using title idx=0, body idx=1)"

            ds_info = ""
            if ds:
                ds_info = f"""
Design System Context:
- Title font: {json.dumps(ds.get('typography', {}).get('titleFont', {}))}
- Body font: {json.dumps(ds.get('typography', {}).get('bodyFont', {}))}
- Palette: {json.dumps(ds.get('palette', {}).get('colors', [])[:5])}
"""

            key_points = outline.get("keyPoints", []) if isinstance(outline, dict) else []
            kp_list = "\n".join(f"  - {kp}" for kp in key_points) if key_points else "  (none)"

            prompt = f"""Generate content for a single presentation slide.

Slide Outline:
{json.dumps(outline, indent=2)}

Key points to cover on this slide:
{kp_list}

Selected Layout: {layout_name}
Available Placeholders:
{ph_text}
Resolved placeholder indices: title={title_idx}, body={body_idx}
{ds_info}

Return a JSON object with this exact structure:
{{
  "slideIndex": {slide_index},
  "layoutId": "{layout.get('layoutId', '1')}",
  "layoutName": "{layout_name}",
  "contents": [
    {{
      "type": "text",
      "placeholderIdx": {title_idx},
      "runs": [
        {{"text": "Slide title here", "bold": true, "bullet": false, "level": 0}}
      ]
    }},
    {{
      "type": "text",
      "placeholderIdx": {body_idx},
      "runs": [
        {{"text": "First key point as a concise sentence", "bold": false, "bullet": true, "level": 0}},
        {{"text": "Second key point as a concise sentence", "bold": false, "bullet": true, "level": 0}},
        {{"text": "Third key point as a concise sentence", "bold": false, "bullet": true, "level": 0}}
      ]
    }}
  ],
  "notes": "Speaker notes for this slide"
}}

Rules:
- Put the slide title in the title placeholder (placeholderIdx={title_idx}).
- Put EVERY key point listed above into the body placeholder (placeholderIdx={body_idx}) as its OWN bullet run - one run per key point, with bullet: true and level: 0. Never collapse multiple key points into a single bullet or drop points to save space.
- Keep each bullet to a single concise sentence; do not pad, but do not omit points.
- Use sub-bullets (level: 1) only when a key point genuinely needs a supporting detail.
- Use "image" type for pic placeholders with {{type: "image", image: {{source: "prompt", data: "description"}}}}.
- Use "table" type for tbl placeholders.
- Each content item must have a placeholderIdx matching one of the available placeholders."""

            response = await llm.chat(
                messages=[{"role": "user", "content": prompt}],
                tools=None,
            )

            result_text = response.get("message", {}).get("content", "{}")
            slide_data = extract_json_object(result_text)
            if slide_data is None:
                # Fail loudly instead of silently falling back to an
                # object with no "contents" - that previously produced a
                # "successful" tool call for a slide with zero content,
                # the exact "right slide count, no content" symptom. A
                # failure here surfaces as a retryable tool_result, so the
                # LLM sees the problem and tries again.
                return ToolResult(
                    success=False,
                    error=(
                        "Could not parse the model's response as slide JSON "
                        f"(response started with: {result_text[:200]!r}). Retry and "
                        "respond with ONLY the JSON object - no prose before or after it."
                    ),
                    duration_ms=int((time.time() - start) * 1000),
                )

            # Ensure required fields
            slide_data.setdefault("slideIndex", slide_index)
            slide_data.setdefault("layoutId", layout.get("layoutId", "1"))
            slide_data.setdefault("layoutName", layout_name)
            slide_data.setdefault("contents", [])

            if not slide_data["contents"]:
                return ToolResult(
                    success=False,
                    error=(
                        "The generated slide has no content items. Include a text "
                        f"item for placeholderIdx={title_idx} (title) and one for "
                        f"placeholderIdx={body_idx} (body, one run per key point)."
                    ),
                    duration_ms=int((time.time() - start) * 1000),
                )

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