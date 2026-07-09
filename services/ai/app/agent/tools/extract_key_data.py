"""
Tool: extract_key_data
Pulls specific data points (metrics, dates, names, figures) from analyzed document content.
"""

from __future__ import annotations
from app.agent.registry import ToolRegistry, ToolResult, ToolParameter
from app.llm.provider import get_tools_llm_provider as get_llm_provider
from app.observability.logging import get_logger
import json
import time

logger = get_logger(__name__)


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
            ],
        )

    @classmethod
    async def run(
        cls,
        content: str,
        data_types: str = "all",
    ) -> ToolResult:
        start = time.time()
        try:
            llm = get_llm_provider()

            prompt = f"""Extract key data points from the following content.
Focus on: {data_types}

Return a JSON object with these keys:
- "metrics": list of {{name, value, unit}} objects
- "key_points": list of important statements
- "dates": list of {{date, context}} objects
- "names": list of {{name, role}} objects
- "figures": list of {{description, value}} objects
- "topics": list of main topics

Content:
{content[:8000]}"""

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