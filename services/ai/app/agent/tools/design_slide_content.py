"""
Tool: design_slide_content
Generates the structured JSON content for a single slide, following the slide JSON schema.
The renderer will consume this to clone layout XML and fill placeholders.
"""

from __future__ import annotations
from app.agent.registry import ToolRegistry, ToolResult, ToolParameter
from app.agent import run_context
from app.agent.run_context import get_chat_provider
from app.agent.tools._json_extract import coerce_json_arg, extract_json_object
from app.llm.provider import get_llm_provider
from app.observability.logging import get_logger
import json
import time

logger = get_logger(__name__)

def _distinct_by_url(findings: list[dict]) -> list[dict]:
    """The reference list, numbered exactly as renderer/citations.py will."""
    ordered, seen = [], set()
    for finding in findings or []:
        if not isinstance(finding, dict):
            continue
        url = (finding.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        ordered.append(finding)
    return ordered


# How much depth each bullet/note should carry, keyed by the composer's
# detail_level. Concise mirrors the original single-sentence-only behaviour;
# the others progressively ask for more elaboration and richer notes.
_DETAIL_GUIDANCE = {
    "concise": (
        "Keep each bullet to a single short, concise sentence; do not pad, "
        "but do not omit points. Speaker notes should be a brief 1-2 sentence "
        "summary."
    ),
    "detailed": (
        "Write each bullet as a fuller sentence that includes a concrete "
        "supporting detail (a number, a cause, an implication) rather than "
        "a bare label - but stay to one sentence per bullet; use sub-bullets "
        "(level: 1) for a second layer of detail when a point warrants it. "
        "Speaker notes should be 2-4 sentences that add context beyond what "
        "the bullets already say."
    ),
    "comprehensive": (
        "Write each bullet as a fuller sentence with concrete supporting "
        "detail, and add a sub-bullet (level: 1) under most points to "
        "elaborate further - aim for real depth, not just restating the key "
        "point in longer words. Speaker notes should be a thorough paragraph "
        "giving the presenter everything they'd need to talk through the "
        "slide without reading it verbatim."
    ),
}


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
                ToolParameter(
                    name="detail_level",
                    type="string",
                    description="Content depth: concise, detailed, or comprehensive",
                    required=False,
                ),
                ToolParameter(
                    name="prepared_visuals",
                    type="string",
                    description=(
                        "JSON list of visual kinds already built and attached to this "
                        "slide by build_chart/build_table/build_key_metrics. "
                        "Auto-supplied by the system - do not pass it yourself."
                    ),
                    required=False,
                ),
                ToolParameter(
                    name="selected_image_context",
                    type="string",
                    description=(
                        "JSON of a pre-vetted image for this slide, if select_source_image "
                        "found a confident match. Optional - auto-supplied by the system when "
                        "present. If given, reference it with "
                        '{"type": "image", "image": {"source": "selected"}} rather than '
                        "inventing image data yourself; if absent, do not include an image."
                    ),
                    required=False,
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
        detail_level: str = "detailed",
        selected_image_context: str = "{}",
        prepared_visuals: str = "[]",
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
            selected_image = coerce_json_arg(selected_image_context, {})
            attached = [
                block for block in (coerce_json_arg(prepared_visuals, []) or [])
                if isinstance(block, dict)
            ]

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

            image_info = (
                "\nA pre-vetted source image IS available for this slide "
                f"(what it shows: {selected_image.get('altText', '') or 'see catalog context'!r}). "
                'If it fits the slide, include {"type": "image", "image": {"source": "selected"}} in contents.'
                if selected_image.get("data")
                else "\nNo pre-vetted source image is available for this slide - do not include image content."
            )

            key_points = outline.get("keyPoints", []) if isinstance(outline, dict) else []
            kp_list = "\n".join(f"  - {kp}" for kp in key_points) if key_points else "  (none)"

            detail_guidance = _DETAIL_GUIDANCE.get(detail_level, _DETAIL_GUIDANCE["detailed"])

            # Only ask for source tags when the deck is actually going to show
            # them and there is research to point at. Asking unconditionally
            # would have the model inventing reference numbers for a deck with
            # no reference list - a marker with nothing behind it is worse than
            # no marker.
            citation_guidance = ""
            findings = run_context.get_research_findings() or []
            if run_context.get_include_sources() and findings:
                numbered = "\n".join(
                    f"  [{i}] {(f.get('title') or f.get('url') or '').strip()[:90]}"
                    for i, f in enumerate(_distinct_by_url(findings), start=1)
                )
                citation_guidance = (
                    "- This deck cites its sources. When a run's content comes from a "
                    "specific finding below, add \"sourceRef\": N to that run, using the "
                    "number in brackets. Tag the run carrying the fact, not every run on "
                    "the slide, and leave a run untagged when it is your own framing "
                    "rather than something you read. Never use a number that is not "
                    "listed.\n"
                    f"  Available sources:\n{numbered}"
                )

            # A visual built through build_chart/build_table/build_key_metrics
            # is attached to the slide server-side. Telling the model it exists
            # (but not what is in it) is what keeps the copy from restating the
            # same figures next to the chart that already shows them.
            visual_info = _prepared_visual_guidance(attached)

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
{image_info}
{visual_info}

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
- {detail_guidance}
- Only use "image" type when a pre-vetted source image was supplied above (source: "selected") - never invent image data yourself; there is no image-generation step, only reuse of images that genuinely already exist.
- Use "table" type for tbl placeholders (or any content placeholder, if no dedicated table placeholder exists) when the content is genuinely tabular (comparisons, structured data with 2+ rows and 2+ columns) - do not force prose bullet points into a table, and do not use a table for anything that reads better as bullets. Shape: {{type: "table", placeholderIdx: N, table: {{headerRow: true, rows: [["Column A", "Column B"], ["Row 1 value", "Row 1 value"], ["Row 2 value", "Row 2 value"]]}}}}. Every row must have the same number of cells as the header row. Keep cell text short (a few words) - tables are rendered at a fixed size, not auto-expanded.
- Use "chart" type whenever the key points for this slide carry numbers that tell a story a reader would rather SEE than read: a trend over time, a comparison across categories, a breakdown of a whole, or a correlation. A slide whose points are mostly figures ("revenue rose from $11.7B to $37.2B across 2011-2025") should render that data as a chart, not as a list of sentences. Shape: {{type: "chart", placeholderIdx: N, chart: {{chartType: "bar"|"line"|"pie"|"scatter", categories: ["2011", "2020", "2025"], series: [{{name: "Revenue ($B)", values: [11.7, 23.5, 37.2]}}]}}}}. Pick chartType by intent: "line" for change over time, "bar" for comparison across categories, "pie" for parts of a single whole (one series, values summing to a meaningful total), "scatter" for relationships between two measures. Every series must have exactly as many values as there are categories, and values must be plain numbers - no units, no "%" or "$" inside the number (put those in the series name). Only chart figures that are actually present in the key points or key data; never invent or estimate values to fill a chart.
- A chart or a table REPLACES the prose it visualizes - do not emit a chart and then also restate the same figures as bullets in the same body placeholder. A short bullet or two of interpretation ("growth flattened after 2023") alongside a chart is good; a duplicate of the chart's own numbers is not.
- Each content item must have a placeholderIdx matching one of the available placeholders.
{citation_guidance}"""

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

def _describe_visual(block: dict) -> str:
    """One line naming what a prepared visual actually shows."""
    kind = block.get("type")
    if kind == "chart":
        chart = block.get("chart") or {}
        series = "; ".join(
            f"{s.get('name')}: {', '.join(str(v) for v in (s.get('values') or [])[:12])}"
            for s in (chart.get("series") or [])
        )
        categories = ", ".join(str(c) for c in (chart.get("categories") or [])[:12])
        return (
            f"a {chart.get('chartType', 'bar')} chart over [{categories}] showing {series}"
        )
    if kind == "table":
        rows = (block.get("table") or {}).get("rows") or []
        header = ", ".join(str(c) for c in (rows[0] if rows else [])[:8])
        return f"a table with columns [{header}] and {max(0, len(rows) - 1)} data rows"
    if kind == "metrics":
        items = (block.get("metrics") or {}).get("items") or []
        figures = "; ".join(f"{i.get('value')} {i.get('label')}" for i in items)
        return f"a row of headline figures: {figures}"
    return str(kind)


def _prepared_visual_guidance(attached: list[dict]) -> str:
    """What to tell the model about visuals already attached to this slide."""
    if not attached:
        return (
            "\nNo chart, table or metric row has been built for this slide. If its "
            "key points are carried by figures, stop and call build_chart, "
            "build_table or build_key_metrics for this slide_index first - do not "
            "write chart or table JSON here yourself."
        )

    described = "\n".join(f"  - {_describe_visual(block)}" for block in attached)
    return (
        "\nThe following has ALREADY been built for this slide and will be placed "
        f"on it automatically:\n{described}\n"
        "Do NOT emit chart, table or metrics content yourself. Write the title and "
        "at most one or two short bullets that INTERPRET this data - what it means, "
        "what changed, what follows from it. Your copy must be about THIS data and "
        "these categories: naming any other subject, segment or figure would "
        "contradict the visual sitting next to it. Do not list its numbers back; "
        "the reader can already see them."
    )
