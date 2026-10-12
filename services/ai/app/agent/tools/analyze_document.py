"""
Tool: analyze_document
Extracts and summarizes content from uploaded documents (PDF, DOCX, XLSX, TXT, JSON, images).

For PDF and DOCX sources, also catalogs embedded images (with the nearby
text as descriptive context) so select_source_image can later reuse one on
a slide when it is a confident, specific match - this is about surfacing
images that already exist in the user's own source material, never
generating new ones.
"""

from __future__ import annotations
from app.agent.tools._today import today_line
from app.agent.registry import ToolRegistry, ToolResult, ToolParameter
from app.agent.run_context import require_chat_provider
from app.observability.logging import get_logger
import os
import json
import time

logger = get_logger(__name__)

# Bounds on embedded-image cataloging: enough to give select_source_image a
# real choice without ballooning context size or wasting time on documents
# that embed dozens of tiny decorative icons/tracking pixels/bullets.
MAX_CATALOG_IMAGES_PER_DOCUMENT = 6
MAX_CATALOG_IMAGE_BYTES = 5 * 1024 * 1024
MIN_CATALOG_IMAGE_DIMENSION_PX = 80
CATALOG_IMAGE_CONTEXT_CHARS = 300

# How much of the extracted content actually reaches downstream tools,
# keyed by the composer's detail_level. "concise" keeps this tool's
# original behavior (a plain hard truncation - cheap and appropriate for
# "just give me the gist"); "detailed"/"comprehensive" summarize instead of
# truncating once content exceeds the ceiling, so material beyond the old
# fixed 15,000-char cutoff still informs planning rather than being
# silently dropped.
_DETAIL_MAX_CHARS = {
    "concise": 15000,
    "detailed": 30000,
    "comprehensive": 50000,
}
_SUMMARIZE_CHUNK_CHARS = 12000


class AnalyzeDocumentTool:
    """Analyze an uploaded document and return a structured summary."""

    @classmethod
    def register(cls, registry: ToolRegistry) -> None:
        registry.register_tool(
            name="analyze_document",
            description=(
                "Extract and summarize content from an uploaded document. "
                "Supports PDF, DOCX, XLSX, TXT, JSON, and images. "
                "Returns a structured summary with key topics, sections, and data points. "
                "For PDF/DOCX sources, also catalogs embedded images with surrounding text as "
                "context, for later reuse via select_source_image."
            ),
            fn=cls.run,
            parameters=[
                ToolParameter(
                    name="document_path",
                    type="string",
                    description="Path to the uploaded document file",
                ),
                ToolParameter(
                    name="analysis_type",
                    type="string",
                    description="Type of analysis: 'summary', 'detailed', or 'structured'",
                    required=False,
                    enum=["summary", "detailed", "structured"],
                ),
                ToolParameter(
                    name="detail_level",
                    type="string",
                    description="How much of the document to surface: concise, detailed, or comprehensive",
                    required=False,
                ),
            ],
        )

    @classmethod
    async def run(
        cls,
        document_path: str,
        analysis_type: str = "summary",
        detail_level: str = "detailed",
    ) -> ToolResult:
        start = time.time()
        try:
            if not os.path.exists(document_path):
                return ToolResult(success=False, error=f"File not found: {document_path}")

            ext = os.path.splitext(document_path)[1].lower()
            content = ""
            images: list[dict] = []
            filename = os.path.basename(document_path)

            if ext == ".pdf":
                content = await cls._extract_pdf(document_path)
                images = await cls._extract_images_pdf(document_path, filename)
            elif ext == ".docx":
                content = await cls._extract_docx(document_path)
                images = await cls._extract_images_docx(document_path, filename)
            elif ext == ".xlsx":
                content = await cls._extract_xlsx(document_path)
            elif ext == ".txt":
                content = await cls._extract_txt(document_path)
            elif ext == ".json":
                content = await cls._extract_json(document_path)
            else:
                content = f"Unsupported file type: {ext}"

            summary_text, truncated, condensed = await cls._condense_for_context_window(content, detail_level)

            duration_ms = int((time.time() - start) * 1000)
            logger.info("document_analyzed", path=document_path, ext=ext, duration_ms=duration_ms)

            return ToolResult(
                success=True,
                data={
                    "document_path": document_path,
                    "file_type": ext,
                    "content_length": len(content),
                    "truncated": truncated,
                    "condensed": condensed,
                    "content": summary_text,
                    # The document as extracted, not the condensed summary the
                    # model reads. A figure on a slide has to be traceable to
                    # an offset inside the source rather than merely to the
                    # file it was in, and the summary is a paraphrase - the
                    # sentence a reader would go looking for is not in it.
                    # Stripped before this payload reaches the model.
                    "sourceText": content,
                    "analysis_type": analysis_type,
                    "detail_level": detail_level,
                    "images": images,
                },
                duration_ms=duration_ms,
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e), duration_ms=int((time.time() - start) * 1000))

    @classmethod
    async def _condense_for_context_window(cls, content: str, detail_level: str) -> tuple[str, bool, bool]:
        """
        Fit `content` within this detail_level's ceiling. Returns
        (final_content, was_truncated, was_condensed_via_summarization).

        "concise" keeps a plain hard truncation. "detailed"/"comprehensive"
        summarize in chunks instead once content exceeds the ceiling, so
        material beyond the old fixed cutoff still reaches planning instead
        of being silently dropped - a chunk that fails to summarize falls
        back to its own raw (shorter) excerpt rather than losing that
        section of the document entirely.
        """
        max_chars = _DETAIL_MAX_CHARS.get(detail_level, _DETAIL_MAX_CHARS["detailed"])
        if len(content) <= max_chars:
            return content, False, False

        if detail_level == "concise":
            return content[:max_chars] + "... [truncated]", True, False

        chunks = [content[i:i + _SUMMARIZE_CHUNK_CHARS] for i in range(0, len(content), _SUMMARIZE_CHUNK_CHARS)]
        llm = require_chat_provider()
        summaries: list[str] = []
        for chunk in chunks:
            try:
                response = await llm.chat(
                    messages=[{"role": "user", "content": (
                        f"{today_line()}\n\n"
                        "Summarize the following document excerpt for use in writing a "
                        "presentation. Preserve concrete facts, figures, names, and dates - "
                        "do not lose specific details in favor of vague generalities.\n\n"
                        f"{chunk}"
                    )}],
                    tools=None,
                )
                summary = (response.get("message", {}).get("content") or "").strip()
                summaries.append(summary or chunk[:1500])
            except Exception as e:
                logger.warning("document_chunk_summarization_failed", error=str(e))
                summaries.append(chunk[:1500])

        condensed = "\n\n".join(s for s in summaries if s)
        if len(condensed) > max_chars:
            condensed = condensed[:max_chars] + "... [truncated]"
        return condensed, True, True

    @staticmethod
    async def _extract_pdf(path: str) -> str:
        from PyPDF2 import PdfReader
        reader = PdfReader(path)
        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
        return "\n\n".join(pages)

    @staticmethod
    async def _extract_docx(path: str) -> str:
        from docx import Document
        doc = Document(path)
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n\n".join(paragraphs)

    @staticmethod
    def _passes_catalog_size_filter(img_bytes: bytes, width: int = 0, height: int = 0) -> bool:
        """Skip decorative icons/bullets/tracking pixels - a real candidate
        photo/chart/illustration is virtually always larger than this."""
        if len(img_bytes) > MAX_CATALOG_IMAGE_BYTES:
            return False
        if width and height:
            return width >= MIN_CATALOG_IMAGE_DIMENSION_PX and height >= MIN_CATALOG_IMAGE_DIMENSION_PX
        try:
            from PIL import Image
            import io
            with Image.open(io.BytesIO(img_bytes)) as im:
                w, h = im.size
            return w >= MIN_CATALOG_IMAGE_DIMENSION_PX and h >= MIN_CATALOG_IMAGE_DIMENSION_PX
        except Exception:
            # Can't determine dimensions - don't block cataloging over it,
            # the size cap above already guards against pathological cases.
            return True

    @staticmethod
    async def _extract_images_pdf(path: str, filename: str) -> list[dict]:
        """Catalog embedded PDF images, paired with that page's text as context."""
        import fitz  # PyMuPDF
        import base64

        images: list[dict] = []
        try:
            doc = fitz.open(path)
        except Exception as e:
            logger.warning("pdf_image_catalog_failed", path=path, error=str(e))
            return images

        try:
            for page_index in range(len(doc)):
                if len(images) >= MAX_CATALOG_IMAGES_PER_DOCUMENT:
                    break
                page = doc[page_index]
                page_text = (page.get_text() or "").strip()
                context = page_text[:CATALOG_IMAGE_CONTEXT_CHARS] or f"Image from page {page_index + 1}"

                for img_index, img in enumerate(page.get_images(full=True)):
                    if len(images) >= MAX_CATALOG_IMAGES_PER_DOCUMENT:
                        break
                    try:
                        base_image = doc.extract_image(img[0])
                    except Exception:
                        continue
                    img_bytes = base_image.get("image")
                    width, height = base_image.get("width", 0), base_image.get("height", 0)
                    if not img_bytes or not AnalyzeDocumentTool._passes_catalog_size_filter(img_bytes, width, height):
                        continue

                    ext = base_image.get("ext", "png")
                    b64 = base64.b64encode(img_bytes).decode("utf-8")
                    images.append({
                        "id": f"doc-{filename}-p{page_index + 1}-{img_index}",
                        "sourceType": "document",
                        "mimeType": f"image/{ext}",
                        "data": f"data:image/{ext};base64,{b64}",
                        "context": context,
                        "sourceDocument": filename,
                        "width": width,
                        "height": height,
                    })
        finally:
            doc.close()
        return images

    @staticmethod
    async def _extract_images_docx(path: str, filename: str) -> list[dict]:
        """Catalog embedded DOCX images, paired with nearby paragraph text as context."""
        from docx import Document
        import base64
        import re

        try:
            doc = Document(path)
        except Exception as e:
            logger.warning("docx_image_catalog_failed", path=path, error=str(e))
            return []

        images: list[dict] = []
        embed_re = re.compile(r'r:embed="(rId\d+)"')
        paragraphs = doc.paragraphs

        for p_index, para in enumerate(paragraphs):
            if len(images) >= MAX_CATALOG_IMAGES_PER_DOCUMENT:
                break
            match = embed_re.search(para._p.xml)
            if not match:
                continue
            try:
                part = doc.part.rels[match.group(1)].target_part
                img_bytes = part.blob
            except Exception:
                continue
            if not img_bytes or not AnalyzeDocumentTool._passes_catalog_size_filter(img_bytes):
                continue

            context_parts = [
                paragraphs[j].text.strip()
                for j in range(max(0, p_index - 1), min(len(paragraphs), p_index + 2))
                if paragraphs[j].text.strip()
            ]
            context = " ".join(context_parts)[:CATALOG_IMAGE_CONTEXT_CHARS] or f"Image near paragraph {p_index + 1}"

            ext = (os.path.splitext(part.partname)[1].lstrip(".") or "png").lower()
            b64 = base64.b64encode(img_bytes).decode("utf-8")
            images.append({
                "id": f"doc-{filename}-para{p_index}",
                "sourceType": "document",
                "mimeType": f"image/{ext}",
                "data": f"data:image/{ext};base64,{b64}",
                "context": context,
                "sourceDocument": filename,
            })
        return images

    @staticmethod
    async def _extract_xlsx(path: str) -> str:
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True, data_only=True)
        sheets_data = []
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            rows = []
            for row in ws.iter_rows(max_row=50, values_only=True):
                cells = [str(c) if c is not None else "" for c in row]
                rows.append(" | ".join(cells))
            sheets_data.append(f"## Sheet: {sheet_name}\n" + "\n".join(rows))
        wb.close()
        return "\n\n".join(sheets_data)

    @staticmethod
    async def _extract_txt(path: str) -> str:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()

    @staticmethod
    async def _extract_json(path: str) -> str:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return json.dumps(data, indent=2, ensure_ascii=False)