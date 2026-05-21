"""
Tool: finalize_and_render_pptx
Takes validated slide JSON array and a template, then renders the final PPTX
by cloning real layout XML from the template, filling placeholder elements
without touching rPr formatting attributes, and repacking.
"""

from __future__ import annotations
from app.agent.registry import ToolRegistry, ToolResult, ToolParameter
from app.renderer.pptx_renderer import PPTXRenderer
from app.observability.logging import get_logger
import json
import time
import os

logger = get_logger(__name__)


class FinalizeAndRenderPptxTool:
    """Render the final PPTX from validated slide JSON and a template."""

    @classmethod
    def register(cls, registry: ToolRegistry) -> None:
        registry.register_tool(
            name="finalize_and_render_pptx",
            description=(
                "Render the final PPTX presentation from validated slide JSON. "
                "Clones real layout XML from the template, fills placeholders "
                "without touching rPr formatting, and produces a downloadable file. "
                "Returns file path and metadata."
            ),
            fn=cls.run,
            parameters=[
                ToolParameter(
                    name="slides_json",
                    type="string",
                    description="JSON array of validated slide objects",
                ),
                ToolParameter(
                    name="template_path",
                    type="string",
                    description="Path to the PPTX/POTX template file",
                    required=False,
                ),
                ToolParameter(
                    name="presentation_metadata",
                    type="string",
                    description="JSON with title, author, subject",
                    required=False,
                ),
                ToolParameter(
                    name="session_id",
                    type="string",
                    description="The chat session ID for associating the generated file",
                ),
            ],
        )

    @classmethod
    async def run(
        cls,
        slides_json: str,
        session_id: str,
        template_path: str = "",
        presentation_metadata: str = "{}",
    ) -> ToolResult:
        start = time.time()
        try:
            slides = json.loads(slides_json) if slides_json else []
            metadata = json.loads(presentation_metadata) if presentation_metadata else {}

            if not slides:
                return ToolResult(
                    success=False,
                    error="No slides provided for rendering",
                    duration_ms=int((time.time() - start) * 1000),
                )

            # Determine output path
            output_dir = os.environ.get("OUTPUT_DIR", "/app/output")
            os.makedirs(output_dir, exist_ok=True)

            import uuid
            output_id = str(uuid.uuid4())
            output_path = os.path.join(output_dir, f"{output_id}.pptx")

            # Render the PPTX
            renderer = PPTXRenderer(
                template_path=template_path if template_path and os.path.exists(template_path) else None,
            )
            result_path = renderer.render(
                slides=slides,
                metadata=metadata,
                output_path=output_path,
            )

            file_size = os.path.getsize(result_path) if os.path.exists(result_path) else 0

            duration_ms = int((time.time() - start) * 1000)
            logger.info(
                "pptx_rendered",
                session_id=session_id,
                slide_count=len(slides),
                file_size=file_size,
                duration_ms=duration_ms,
            )

            return ToolResult(
                success=True,
                data={
                    "filePath": result_path,
                    "fileName": metadata.get("title", "presentation") + ".pptx",
                    "fileSizeBytes": file_size,
                    "slideCount": len(slides),
                    "sessionId": session_id,
                },
                duration_ms=duration_ms,
            )
        except Exception as e:
            logger.error("pptx_render_failed", error=str(e))
            return ToolResult(success=False, error=str(e), duration_ms=int((time.time() - start) * 1000))