"""
Tool: validate_rendered_output

Ground-truth validation of the already-rendered .pptx: converts it to PDF
via the same LibreOffice call PreviewGenerator already makes (reusing the
cached PDF when available, so this doesn't pay for a second slow
conversion) and inspects the ACTUAL rendered text geometry via PyMuPDF,
flagging any text that overflows its slide or overlaps other content - not
an estimate of what might happen, what LibreOffice really produced.

No PowerPoint-native engine check is performed: true PowerPoint rendering
needs Windows or a paid cloud rendering API this stack does not have.
LibreOffice's rendered geometry is treated as the authoritative source of
truth here, matching how PreviewGenerator (and the browser preview, once
proxied - see Stage A) already work.
"""

from __future__ import annotations
import os
import base64
import shutil
import tempfile
import time

from app.agent.registry import ToolRegistry, ToolResult, ToolParameter
from app.agent.tools._json_extract import extract_json_object
from app.observability.logging import get_logger
from app.renderer.preview_generator import PreviewGenerator

logger = get_logger(__name__)

# A span is flagged as overflowing once it extends this many points past the
# slide boundary - small enough to catch real overflow, large enough to
# absorb PDF-export rounding noise.
OVERFLOW_TOLERANCE_PT = 2.0
# Two spans are flagged as overlapping once their intersection covers this
# fraction of the smaller span's area. Consecutive lines of normally-wrapped
# text within one placeholder empirically overlap by ~13% (font ascent/
# descent bleeding into the adjacent line's box), so this sits comfortably
# above that to avoid flagging ordinary line spacing as a defect.
OVERLAP_AREA_RATIO_THRESHOLD = 0.35
# Vision review is a per-slide LLM call; cap it so a large deck doesn't turn
# "comprehensive" into a slow, expensive validation step.
MAX_VISION_REVIEW_SLIDES = 8


class ValidateRenderedOutputTool:
    """Validate the actually-rendered PPTX output (LibreOffice geometry - ground truth)."""

    @classmethod
    def register(cls, registry: ToolRegistry) -> None:
        registry.register_tool(
            name="validate_rendered_output",
            description=(
                "Validate the ALREADY-RENDERED .pptx output by inspecting the real "
                "LibreOffice-rendered geometry (not an estimate) for text that overflows "
                "its slide or overlaps other content. Call this AFTER finalize_and_render_pptx, "
                "not before. Reports concrete, per-slide issues if any are found - if issues are "
                "found for a slide, redesign that slide with design_slide_content using the "
                "reported detail, then re-render and validate again (bounded retries - if the "
                "limit is reached, accept the output and finish rather than looping forever)."
            ),
            fn=cls.run,
            parameters=[
                ToolParameter(name="pptx_path", type="string", description="Path to the rendered .pptx file"),
                ToolParameter(
                    name="session_id",
                    type="string",
                    description="Session ID - used to find the already-generated preview PDF, avoiding a second LibreOffice conversion",
                ),
                ToolParameter(
                    name="detail_level",
                    type="string",
                    description=(
                        "Content depth level. Optional - auto-supplied by the system. At "
                        "'comprehensive', also runs a vision-LLM aesthetic pass on each slide "
                        "screenshot when a vision-capable provider is configured."
                    ),
                    required=False,
                ),
            ],
        )

    @classmethod
    async def run(cls, pptx_path: str, session_id: str, detail_level: str = "detailed") -> ToolResult:
        start = time.time()
        try:
            if not os.path.exists(pptx_path):
                return ToolResult(
                    success=False, error=f"File not found: {pptx_path}",
                    duration_ms=int((time.time() - start) * 1000),
                )

            preview_gen = PreviewGenerator()
            pdf_path = preview_gen.get_pdf_path(session_id)
            cleanup_dir = None
            if not pdf_path:
                cleanup_dir = tempfile.mkdtemp(prefix="thinkslides_validate_")
                pdf_path = preview_gen.convert_to_pdf(pptx_path, cleanup_dir)

            if not pdf_path or not os.path.exists(pdf_path):
                if cleanup_dir:
                    shutil.rmtree(cleanup_dir, ignore_errors=True)
                return ToolResult(
                    success=False,
                    error="Could not convert the rendered .pptx to PDF for validation (LibreOffice conversion failed).",
                    duration_ms=int((time.time() - start) * 1000),
                )

            try:
                issues = cls._check_geometry(pdf_path)
                if detail_level == "comprehensive":
                    issues.extend(await cls._check_aesthetics(pdf_path))
            finally:
                if cleanup_dir:
                    shutil.rmtree(cleanup_dir, ignore_errors=True)

            duration_ms = int((time.time() - start) * 1000)
            valid = len(issues) == 0
            logger.info(
                "validate_rendered_output",
                session_id=session_id,
                valid=valid,
                issue_count=len(issues),
                duration_ms=duration_ms,
            )

            return ToolResult(
                success=True,
                data={"sessionId": session_id, "valid": valid, "issueCount": len(issues), "issues": issues},
                duration_ms=duration_ms,
            )
        except Exception as e:
            logger.error("validate_rendered_output_failed", error=str(e))
            return ToolResult(success=False, error=str(e), duration_ms=int((time.time() - start) * 1000))

    @staticmethod
    def _check_geometry(pdf_path: str) -> list[dict]:
        """
        The ground-truth check: read every text span's actual bounding box
        as LibreOffice laid it out (PyMuPDF's structured text extraction),
        and flag anything extending past the slide or overlapping another
        span from a different text block. Spans within the same block are
        never compared against each other - that's normal multi-line
        wrapping within one placeholder, not a defect.
        """
        import fitz

        issues: list[dict] = []
        doc = fitz.open(pdf_path)
        try:
            for page_index in range(len(doc)):
                page = doc[page_index]
                page_width, page_height = page.rect.width, page.rect.height
                text_dict = page.get_text("dict")

                # PyMuPDF's own "block" grouping is a text-layout heuristic,
                # not a reliable proxy for "which PPTX shape this came from"
                # - two spans from genuinely different placeholders that
                # happen to sit close together can be merged into one block
                # (confirmed empirically), which would silently suppress a
                # real overlap if blocks were used to exempt pairs from
                # comparison. The overlap-area-ratio threshold alone is what
                # distinguishes normal line-wrapping proximity (empirically
                # ~13% bbox overlap between consecutive wrapped lines, from
                # font ascent/descent) from a genuine overlap - so every
                # span pair is compared, regardless of block.
                spans: list[tuple[tuple, str]] = []
                for block in text_dict.get("blocks", []):
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            text = (span.get("text") or "").strip()
                            if not text:
                                continue
                            spans.append((tuple(span["bbox"]), text))

                for bbox, text in spans:
                    x0, y0, x1, y1 = bbox
                    if (
                        x0 < -OVERFLOW_TOLERANCE_PT or y0 < -OVERFLOW_TOLERANCE_PT
                        or x1 > page_width + OVERFLOW_TOLERANCE_PT
                        or y1 > page_height + OVERFLOW_TOLERANCE_PT
                    ):
                        issues.append({
                            "slideIndex": page_index,
                            "type": "overflow",
                            "detail": f"Text extends past the slide boundary: {text[:60]!r}",
                        })

                for i in range(len(spans)):
                    bbox_i, text_i = spans[i]
                    for j in range(i + 1, len(spans)):
                        bbox_j, text_j = spans[j]
                        inter = _rect_intersection_area(bbox_i, bbox_j)
                        if inter <= 0:
                            continue
                        smaller_area = min(_rect_area(bbox_i), _rect_area(bbox_j))
                        if smaller_area > 0 and inter / smaller_area >= OVERLAP_AREA_RATIO_THRESHOLD:
                            issues.append({
                                "slideIndex": page_index,
                                "type": "overlap",
                                "detail": f"Overlapping content: {text_i[:40]!r} overlaps {text_j[:40]!r}",
                            })
        finally:
            doc.close()
        return issues

    @staticmethod
    async def _check_aesthetics(pdf_path: str) -> list[dict]:
        """
        Best-effort qualitative pass via a vision-capable LLM - purely
        additive. Any failure (no usable provider, network error, bad
        response) is caught and logged, never surfaced as a validation
        failure - the geometric check above is the one thing this tool
        guarantees.
        """
        issues: list[dict] = []
        try:
            from app.llm import get_vision_llm_provider
            import fitz

            vision_provider = get_vision_llm_provider()
            doc = fitz.open(pdf_path)
            try:
                page_count = min(len(doc), MAX_VISION_REVIEW_SLIDES)
                for page_index in range(page_count):
                    page = doc[page_index]
                    pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
                    img_b64 = base64.b64encode(pix.tobytes("png")).decode("utf-8")

                    response = await vision_provider.chat_with_images(
                        prompt=(
                            "Look at this presentation slide screenshot. Reply with ONLY a JSON "
                            'object: {"hasIssue": true|false, "issue": "one short sentence, or '
                            'empty string"}. Only flag a genuine visual defect - overlapping '
                            "text, text spilling off the slide, illegibly low contrast - not "
                            "subjective style preferences."
                        ),
                        images=[f"data:image/png;base64,{img_b64}"],
                        temperature=0.2,
                        max_tokens=200,
                    )
                    parsed = extract_json_object(response)
                    if parsed and parsed.get("hasIssue") and parsed.get("issue"):
                        issues.append({
                            "slideIndex": page_index,
                            "type": "aesthetic",
                            "detail": str(parsed["issue"])[:200],
                        })
            finally:
                doc.close()
        except Exception as e:
            logger.warning("vision_aesthetic_review_skipped", error=str(e))
        return issues


def _rect_intersection_area(a: tuple, b: tuple) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1 - x0) * (y1 - y0)


def _rect_area(r: tuple) -> float:
    return max(0.0, r[2] - r[0]) * max(0.0, r[3] - r[1])
