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
from app.agent.registry import ToolRegistry, ToolResult, ToolParameter
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
            ],
        )

    @classmethod
    async def run(
        cls,
        document_path: str,
        analysis_type: str = "summary",
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

            # Truncate for LLM context window
            max_chars = 15000
            truncated = len(content) > max_chars
            summary_text = content[:max_chars] + ("... [truncated]" if truncated else "")

            duration_ms = int((time.time() - start) * 1000)
            logger.info("document_analyzed", path=document_path, ext=ext, duration_ms=duration_ms)

            return ToolResult(
                success=True,
                data={
                    "document_path": document_path,
                    "file_type": ext,
                    "content_length": len(content),
                    "truncated": truncated,
                    "content": summary_text,
                    "analysis_type": analysis_type,
                    "images": images,
                },
                duration_ms=duration_ms,
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e), duration_ms=int((time.time() - start) * 1000))

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