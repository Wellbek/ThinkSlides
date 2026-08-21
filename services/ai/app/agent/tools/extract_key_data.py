"""
Tool: extract_key_data
Pulls specific data points (metrics, dates, names, figures) from analyzed document content.
"""

from __future__ import annotations
from app.agent.registry import ToolRegistry, ToolResult, ToolParameter
from app.agent.run_context import require_chat_provider
from app.agent.tools._today import today_line
from app.agent.tools._json_extract import extract_json_object
from app.observability.logging import get_logger
import time

logger = get_logger(__name__)

# How thoroughly to mine the source content, keyed by the composer's
# detail_level - mirrors the pattern in plan_presentation_structure.py /
# design_slide_content.py. Concise keeps this tool's original "headline
# facts only" behavior; the others progressively ask for more items per
# category and widen how much source content is actually considered.
_DETAIL_GUIDANCE = {
    "concise": (
        "Extract only the handful of most important items per category "
        "(up to 5 each) - the headline facts, not exhaustive detail."
    ),
    "detailed": (
        "Extract a solid, representative set of items per category (up to "
        "10 each), including secondary facts that are still genuinely "
        "useful for a presentation."
    ),
    "comprehensive": (
        "Extract as many genuinely relevant items per category as the "
        "content actually supports (up to 20 each) - be thorough. This "
        "feeds a comprehensive-depth presentation that should not have to "
        "go back to the source document to find something you skipped."
    ),
}

# How much of the source content to actually consider, in lockstep with
# analyze_document's own detail-level truncation ceiling.
_DETAIL_MAX_CHARS = {
    "concise": 8000,
    "detailed": 16000,
    "comprehensive": 28000,
}


class ExtractKeyDataTool:
    """Extract specific data points from document content using LLM."""

    @classmethod
    def register(cls, registry: ToolRegistry) -> None:
        registry.register_tool(
            name="extract_key_data",
            description=(
                "Extract specific data points from document content. "
                "Returns structured JSON with key metrics, dates, names, figures, "
                "and any other data points relevant to presentation generation."
            ),
            fn=cls.run,
            parameters=[
                ToolParameter(
                    name="content",
                    type="string",
                    description="The document content to extract data from",
                ),
                ToolParameter(
                    name="data_types",
                    type="string",
                    description="Comma-separated list of data types to extract: metrics,dates,names,figures,all",
                    required=False,
                ),
                ToolParameter(
                    name="detail_level",
                    type="string",
                    description="How thoroughly to mine the content: concise, detailed, or comprehensive",
                    required=False,
                ),
            ],
        )

    @classmethod
    async def run(
        cls,
        content: str,
        data_types: str = "all",
        detail_level: str = "detailed",
    ) -> ToolResult:
        start = time.time()
        try:
            # The workspace's configured model. There is no environment
            # fallback: a run reaches a tool only after the loop resolved
            # Settings into a provider, and its absence is an error, not a
            # cue to quietly use somebody else's key.
            llm = require_chat_provider()

            detail_guidance = _DETAIL_GUIDANCE.get(detail_level, _DETAIL_GUIDANCE["detailed"])
            max_chars = _DETAIL_MAX_CHARS.get(detail_level, _DETAIL_MAX_CHARS["detailed"])

            prompt = f"""{today_line()}

Extract key data points from the following content.
Focus on: {data_types}

{detail_guidance}

Return a JSON object and nothing else, with these keys:
- "metrics": list of {{name, value, unit}} objects
- "key_points": list of important statements
- "dates": list of {{date, context}} objects
- "names": list of {{name, role}} objects
- "figures": list of {{description, value}} objects
- "topics": list of main topics

Content:
{content[:max_chars]}"""

            response = await llm.chat(
                messages=[{"role": "user", "content": prompt}],
                tools=None,
            )

            result_text = response.get("message", {}).get("content", "") or ""
            # Models fence their JSON, or introduce it with a sentence. A bare
            # json.loads survives neither, and the fallback here used to be an
            # object with none of the expected keys - which the activity feed
            # then reported as "found no structured data points", indefinitely
            # re-triable and indistinguishable from an empty document.
            data = extract_json_object(result_text)
            if data is None:
                return ToolResult(
                    success=False,
                    error=(
                        "The extraction did not come back as JSON. Retry with a "
                        "smaller slice of the content, or work from what "
                        "analyze_document already returned."
                    ),
                    duration_ms=int((time.time() - start) * 1000),
                )

            duration_ms = int((time.time() - start) * 1000)
            return ToolResult(success=True, data=data, duration_ms=duration_ms)

        except Exception as e:
            return ToolResult(success=False, error=str(e), duration_ms=int((time.time() - start) * 1000))
