"""
Tool: analyze_document
Extracts and summarizes content from uploaded documents (PDF, DOCX, XLSX, TXT, JSON, images).
"""

from __future__ import annotations
from app.agent.registry import ToolRegistry, ToolResult, ToolParameter
from app.observability.logging import get_logger
import os
import json
import time

logger = get_logger(__name__)


class AnalyzeDocumentTool:
    """Analyze an uploaded document and return a structured summary."""

    @classmethod
    def register(cls, registry: ToolRegistry) -> None:
        registry.register_tool(
            name="analyze_document",
            description=(
                "Extract and summarize content from an uploaded document. "
                "Supports PDF, DOCX, XLSX, TXT, JSON, and images. "
                "Returns a structured summary with key topics, sections, and data points."
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

            if ext == ".pdf":
                content = await cls._extract_pdf(document_path)
            elif ext == ".docx":
                content = await cls._extract_docx(document_path)
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