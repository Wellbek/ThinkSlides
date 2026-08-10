"""
Tool: extract_key_data
Pulls specific data points (metrics, dates, names, figures) from analyzed document content.
"""

from __future__ import annotations
from app.agent.registry import ToolRegistry, ToolResult, ToolParameter
from app.agent.run_context import get_chat_provider
from app.llm.provider import get_tools_llm_provider as get_llm_provider
from app.observability.logging import get_logger
import json
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
            # An organisation that configured its own provider gets it here
            # too: its explicit choice outranks the environment's cheaper
            # tools-tier model, which only exists for the default provider.
            llm = get_chat_provider() or get_llm_provider()

            detail_guidance = _DETAIL_GUIDANCE.get(detail_level, _DETAIL_GUIDANCE["detailed"])
            max_chars = _DETAIL_MAX_CHARS.get(detail_level, _DETAIL_MAX_CHARS["detailed"])

            prompt = f"""Extract key data points from the following content.
Focus on: {data_types}

{detail_guidance}

Return a JSON object with these keys:
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

            result_text = response.get("message", {}).get("content", "{}")
            try:
                data = json.loads(result_text)
            except json.JSONDecodeError:
                data = {"raw_extraction": result_text}

            duration_ms = int((time.time() - start) * 1000)
            return ToolResult(success=True, data=data, duration_ms=duration_ms)

        except Exception as e:
            return ToolResult(success=False, error=str(e), duration_ms=int((time.time() - start) * 1000))
