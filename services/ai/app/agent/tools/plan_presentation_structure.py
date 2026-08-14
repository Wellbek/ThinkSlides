"""
Tool: plan_presentation_structure
Plans the overall slide structure and outline based on analyzed content and requirements.
"""

from __future__ import annotations
from app.agent.registry import ToolRegistry, ToolResult, ToolParameter
from app.agent.run_context import get_chat_provider
from app.agent.tools._json_extract import coerce_json_arg, extract_json_object
from app.llm.provider import get_tools_llm_provider as get_llm_provider
from app.observability.logging import get_logger
import json
import time

logger = get_logger(__name__)

# How much content depth to plan for, keyed by the composer's detail_level.
_DETAIL_GUIDANCE = {
    "concise": (
        "Each content slide should have 3-4 key points maximum. Favor fewer, "
        "punchier slides over splitting a topic further than it needs."
    ),
    "detailed": (
        "Each content slide should have 4-6 key points. When a topic has more "
        "to say than comfortably fits one slide's points, add another slide "
        "rather than cramming everything in - err toward a few more slides "
        "over thin ones."
    ),
    "comprehensive": (
        "Each content slide should have 5-8 key points, using the twoColumn "
        "contentType to fit them when the layout catalog offers it. Break "
        "large topics into multiple slides freely - a comprehensive deck is "
        "expected to run longer than a concise one, and totalSlides should "
        "reflect that. Lean harder on charts and tables than at lower detail "
        "levels: where a topic has both a narrative and figures behind it, a "
        "comprehensive deck can afford a dedicated chart or table slide "
        "alongside the prose one rather than choosing between them."
    ),
}


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
                    description=(
                        "JSON summary of the design system layout catalog. Optional - "
                        "the system supplies the current design system automatically, "
                        "so you do not need to pass this."
                    ),
                    required=False,
                ),
                ToolParameter(
                    name="detail_level",
                    type="string",
                    description="Content depth: concise, detailed, or comprehensive",
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
        detail_level: str = "detailed",
    ) -> ToolResult:
        start = time.time()
        try:
            # An organisation that configured its own provider gets it here
            # too: its explicit choice outranks the environment's cheaper
            # tools-tier model, which only exists for the default provider.
            llm = get_chat_provider() or get_llm_provider()

            # coerce_json_arg, not a bare json.loads: a tool-calling model may
            # send a native JSON object for these despite their declared
            # "string" type, which raises TypeError rather than the
            # JSONDecodeError this used to guard against. Both are
            # supplementary context (not required for the plan to succeed),
            # so a soft {} fallback on genuine failure is fine here.
            parsed_data = coerce_json_arg(key_data, {})
            parsed_ds = coerce_json_arg(design_system_summary, {})

            available_layouts = parsed_ds.get("layouts", [])
            layout_info = ""
            if available_layouts:
                layout_names = [lt.get("layoutName", f"Layout {lt.get('layoutId', '?')}") for lt in available_layouts]
                layout_info = f"\nAvailable layout names in template: {', '.join(layout_names)}"

            detail_guidance = _DETAIL_GUIDANCE.get(detail_level, _DETAIL_GUIDANCE["detailed"])

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
      "section": "Which part of the deck this slide belongs to",
      "titleRegister": "claim|question|label",
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
- Every slide that has key points MUST use a contentType whose layout has a body placeholder: "content" (one body), "twoColumn" (two bodies), "chart", "table", or "image". Never assign 3-5 key points to a "title" or "conclusion" slide - those layouts have no body placeholder and the points would be dropped.
- Give every slide a "titleRegister", which says what its headline is FOR:
  * "claim" - the slide carries a finding and its headline states it: "Applied AI recorded the highest innovation score of all 14 trends". A reader who reads only the claim headlines of a deck has read its argument. Use this for any slide backed by a chart, a table, figures or a comparison.
  * "question" - the headline asks something the slide answers, repeated in the same form across comparable slides. Use this when the deck has a repeating structure ("Why should leaders pay attention?" asked of every subject).
  * "label" - the headline names the topic. Correct for a cover, a section break, a divider, and for any slide that genuinely has no finding to state.
  Do NOT mark a slide "claim" unless it has something to state a finding FROM. A confident headline over a slide with no figures is the deck lying about its own evidence, and it is rejected at validation.
- Give every slide a "section": the part of the deck it belongs to, named in two or three words and REPEATED verbatim across every slide in that part. Sections are what let a reader tell where they are in the deck, and they are printed as a small eyebrow on each slide - so "Market context" on four consecutive slides is right, and four differently-worded variations of it is not. A deck short enough to have no parts can use one section for all of them.
- Distribute content evenly across slides
- Judge every slide against the data behind it, not just its word count. If a slide's key points are carried by numbers - a trend over time, a comparison across categories, a breakdown of a whole - give it contentType "chart". If they are carried by structured rows and columns, give it contentType "table". A deck that states figures only as prose bullets when a chart would show them is a worse deck at every level of detail.
- KPIs, metrics, financial results, growth rates, targets vs actuals and year-on-year figures are chart material by default. If the user asked for KPIs or metrics, or the key data is a set of numbers, at least one slide MUST have contentType "chart" - returning a deck of prose bullets for a numbers request is a failure, not a stylistic choice. A "chart" slide still keeps its keyPoints: they become the short interpretation alongside the visual.
- Do not manufacture visuals. Only propose "chart" or "table" where the analyzed content genuinely supplies the underlying numbers or rows; a topic with no real data should stay prose.
- {detail_guidance}
- Choose suggestedLayout names from the available layouts if a template is provided"""

            response = await llm.chat(
                messages=[{"role": "user", "content": prompt}],
                tools=None,
            )

            result_text = response.get("message", {}).get("content", "{}")
            structure = extract_json_object(result_text)
            if structure is None or not structure.get("slides"):
                # Fail loudly rather than silently proceeding with a
                # {"raw_plan": ...} object that has no totalSlides/slides -
                # every downstream step depends on this structure existing.
                return ToolResult(
                    success=False,
                    error=(
                        "Could not parse the model's response as a presentation "
                        f"structure (response started with: {result_text[:200]!r}). "
                        "Retry and respond with ONLY the JSON object - no prose "
                        "before or after it - including a non-empty 'slides' array."
                    ),
                    duration_ms=int((time.time() - start) * 1000),
                )

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