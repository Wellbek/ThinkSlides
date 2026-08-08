"""
Tool: finalize_and_render_pptx
Takes validated slide JSON array and a template, then renders the final PPTX
by cloning real layout XML from the template, filling placeholder elements
without touching rPr formatting attributes, and repacking.
Also generates preview images for each slide.
"""

from __future__ import annotations
from app.agent.registry import ToolRegistry, ToolResult, ToolParameter
from app.renderer.pptx_renderer import PPTXRenderer
from app.renderer.preview_generator import PreviewGenerator
from app.observability.logging import get_logger
import json
import time
import os
import uuid

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
                ToolParameter(
                    name="color_theme",
                    type="string",
                    description=(
                        "Color palette to use, one of: professional, warm, monochrome, "
                        "bold, nature. Only takes effect when no template_path is given "
                        "- an uploaded template's own colors are always preserved."
                    ),
                    required=False,
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
        color_theme: str = "",
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

            # Determine output paths
            output_dir = os.environ.get("OUTPUT_DIR", "/app/output")
            os.makedirs(output_dir, exist_ok=True)

            output_id = str(uuid.uuid4())
            output_path = os.path.join(output_dir, f"{output_id}.pptx")
            markdown_path = os.path.join(output_dir, f"{output_id}.md")

            # Generate fallback markdown first (so we always have something to give the user)
            fallback_markdown = cls._generate_markdown_fallback(slides, metadata)
            with open(markdown_path, "w", encoding="utf-8") as f:
                f.write(fallback_markdown)

            # Retry logic for rendering (3 attempts)
            result_path = None
            last_error = None
            max_retries = 3

            for attempt in range(1, max_retries + 1):
                try:
                    renderer = PPTXRenderer(
                        template_path=template_path if template_path and os.path.exists(template_path) else None,
                        color_theme=color_theme or None,
                    )
                    result_path = renderer.render(
                        slides=slides,
                        metadata=metadata,
                        output_path=output_path,
                    )

                    # Verify the file was created
                    if not os.path.exists(result_path) or os.path.getsize(result_path) == 0:
                        raise Exception("PPTX file was not created or is empty")

                    logger.info(
                        "pptx_render_success",
                        session_id=session_id,
                        slide_count=len(slides),
                        attempt=attempt,
                        max_retries=max_retries,
                    )
                    break

                except Exception as e:
                    last_error = e
                    logger.warning(
                        "pptx_render_attempt_failed",
                        session_id=session_id,
                        attempt=attempt,
                        max_retries=max_retries,
                        error=str(e),
                    )

                    # Clean up failed attempt
                    if os.path.exists(output_path):
                        os.remove(output_path)

                    if attempt < max_retries:
                        time.sleep(1)  # Brief pause before retry

            # If all retries failed, return the fallback
            if result_path is None:
                logger.error(
                    "pptx_render_all_retries_failed",
                    session_id=session_id,
                    error=str(last_error),
                )

                # Return the markdown fallback as the primary result
                return ToolResult(
                    success=False,
                    error=f"PPTX rendering failed after {max_retries} attempts: {str(last_error)}. A markdown fallback has been generated.",
                    data={
                        "fallbackMarkdown": fallback_markdown,
                        "fallbackMarkdownPath": markdown_path,
                        "fallbackSlidesJson": slides_json,
                        "slides": slides,
                        "metadata": metadata,
                        "sessionId": session_id,
                    },
                    duration_ms=int((time.time() - start) * 1000),
                )

            file_size = os.path.getsize(result_path) if os.path.exists(result_path) else 0

            # Generate preview images
            preview_gen = PreviewGenerator()
            preview_urls = preview_gen.generate_previews(result_path, session_id)

            duration_ms = int((time.time() - start) * 1000)
            logger.info(
                "pptx_rendered",
                session_id=session_id,
                slide_count=len(slides),
                file_size=file_size,
                preview_count=len(preview_urls),
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
                    "previewUrls": preview_urls,
                    "fallbackMarkdownPath": markdown_path,
                },
                duration_ms=duration_ms,
            )
        except Exception as e:
            logger.error("pptx_render_failed", error=str(e))
            return ToolResult(success=False, error=str(e), duration_ms=int((time.time() - start) * 1000))

    @classmethod
    def _generate_markdown_fallback(cls, slides: list[dict], metadata: dict) -> str:
        """Generate a markdown representation of the slides as fallback."""
        lines = []

        # Header with metadata
        title = metadata.get("title", "Presentation")
        author = metadata.get("author", "")
        subject = metadata.get("subject", "")

        lines.append(f"# {title}\n")
        if author:
            lines.append(f"**Author:** {author}\n")
        if subject:
            lines.append(f"**Subject:** {subject}\n")
        lines.append("\n---\n\n")

        # Add each slide
        for slide in slides:
            slide_idx = slide.get("slideIndex", 0)
            layout_name = slide.get("layoutName", slide.get("layoutId", "Unknown"))

            lines.append(f"## Slide {slide_idx + 1}: {layout_name}\n")

            # Add slide content
            contents = slide.get("contents", [])
            for content in contents:
                if content.get("type") == "text":
                    runs = content.get("runs", [])
                    for run in runs:
                        text = run.get("text", "")
                        is_bold = run.get("bold", False)
                        is_bullet = run.get("bullet", False)
                        level = run.get("level", 0)

                        if text:
                            if is_bullet:
                                indent = "  " * level
                                lines.append(f"{indent}- {text}")
                            elif is_bold:
                                lines.append(f"**{text}**")
                            else:
                                lines.append(text)

            # Add notes
            notes = slide.get("notes", "")
            if notes:
                lines.append(f"\n**Notes:** {notes}")

            lines.append("\n---\n\n")

        # Add footer with instructions
        lines.append("---\n\n")
        lines.append("*This presentation was generated by ThinkSlides. ")
        lines.append("Due to a technical issue with PPTX rendering, this markdown fallback is provided. ")
        lines.append("You can use this content to manually create your presentation in PowerPoint, Google Slides, or any other presentation software.*")

        return "\n".join(lines)