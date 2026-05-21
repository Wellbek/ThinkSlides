"""
Document Ingestion Worker Pool

Runs as an async Python worker pool using asyncio and concurrent.futures.
Each uploaded file is chunked, embedded, and stored in PostgreSQL pgvector
for RAG retrieval during content generation.
"""

from __future__ import annotations
import asyncio
import os
import json
import uuid
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from app.observability.logging import get_logger

logger = get_logger(__name__)

# Chunking defaults
CHUNK_SIZE_CHARS = 1500
CHUNK_OVERLAP_CHARS = 200
MAX_CONCURRENT_INGESTIONS = 4


class IngestionWorker:
    """
    Async worker pool for document ingestion.
    Chunks documents, generates embeddings, and stores in pgvector.
    """

    def __init__(self, db_session_factory, max_workers: int = MAX_CONCURRENT_INGESTIONS) -> None:
        self.db_session_factory = db_session_factory
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.semaphore = asyncio.Semaphore(max_workers)

    async def ingest_file(
        self,
        file_path: str,
        organisation_id: uuid.UUID,
        session_id: Optional[uuid.UUID] = None,
        mime_type: str = "",
    ) -> dict:
        """
        Ingest a single file: extract text, chunk, embed, and store.

        Returns a dict with document_id, chunk_count, and status.
        """
        async with self.semaphore:
            start = time.time()
            try:
                # Step 1: Extract text content
                text = await asyncio.get_event_loop().run_in_executor(
                    self.executor, self._extract_text, file_path, mime_type
                )

                if not text or not text.strip():
                    return {"status": "empty", "chunk_count": 0}

                # Step 2: Chunk the text
                chunks = self._chunk_text(text)
                chunk_count = len(chunks)

                # Step 3: Generate embeddings (placeholder — in production, call embedding API)
                embeddings = await self._generate_embeddings(chunks)

                # Step 4: Store in database
                doc_id = await self._store_chunks(
                    file_path=file_path,
                    organisation_id=organisation_id,
                    session_id=session_id,
                    chunks=chunks,
                    embeddings=embeddings,
                )

                duration_ms = int((time.time() - start) * 1000)
                logger.info(
                    "document_ingested",
                    file_path=file_path,
                    chunk_count=chunk_count,
                    duration_ms=duration_ms,
                    doc_id=str(doc_id),
                )

                return {
                    "document_id": str(doc_id),
                    "chunk_count": chunk_count,
                    "status": "completed",
                }
            except Exception as e:
                logger.error("ingestion_failed", file_path=file_path, error=str(e))
                return {"status": "failed", "error": str(e)}

    def _extract_text(self, file_path: str, mime_type: str) -> str:
        """Extract text content from a file (runs in thread pool)."""
        ext = os.path.splitext(file_path)[1].lower()

        if ext == ".pdf":
            return self._extract_pdf(file_path)
        elif ext == ".docx":
            return self._extract_docx(file_path)
        elif ext == ".xlsx":
            return self._extract_xlsx(file_path)
        elif ext == ".txt":
            return self._extract_txt(file_path)
        elif ext == ".json":
            return self._extract_json(file_path)
        else:
            logger.warning("unsupported_file_type", ext=ext)
            return ""

    @staticmethod
    def _extract_pdf(path: str) -> str:
        from PyPDF2 import PdfReader
        reader = PdfReader(path)
        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
        return "\n\n".join(pages)

    @staticmethod
    def _extract_docx(path: str) -> str:
        from docx import Document
        doc = Document(path)
        return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())

    @staticmethod
    def _extract_xlsx(path: str) -> str:
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True, data_only=True)
        sheets = []
        for name in wb.sheetnames:
            ws = wb[name]
            rows = []
            for row in ws.iter_rows(max_row=100, values_only=True):
                rows.append(" | ".join(str(c) if c is not None else "" for c in row))
            sheets.append(f"Sheet: {name}\n" + "\n".join(rows))
        wb.close()
        return "\n\n".join(sheets)

    @staticmethod
    def _extract_txt(path: str) -> str:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()

    @staticmethod
    def _extract_json(path: str) -> str:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return json.dumps(data, indent=2, ensure_ascii=False)

    @staticmethod
    def _chunk_text(text: str, chunk_size: int = CHUNK_SIZE_CHARS, overlap: int = CHUNK_OVERLAP_CHARS) -> list[str]:
        """Split text into overlapping chunks for embedding."""
        if len(text) <= chunk_size:
            return [text]

        chunks = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            chunk = text[start:end]
            chunks.append(chunk)
            start += chunk_size - overlap

        return chunks

    async def _generate_embeddings(self, chunks: list[str]) -> list[Optional[list[float]]]:
        """
        Generate embeddings for each chunk.
        Uses the configured LLM provider's embedding API.
        Currently returns None placeholders — replace with actual embedding calls.
        """
        # TODO: Integrate with Zhipu embedding-2 or Azure text-embedding-ada-002
        # For now, return None for each chunk (pgvector will use NULL)
        return [None] * len(chunks)

    async def _store_chunks(
        self,
        file_path: str,
        organisation_id: uuid.UUID,
        session_id: Optional[uuid.UUID],
        chunks: list[str],
        embeddings: list[Optional[list[float]]],
    ) -> uuid.UUID:
        """Store document and chunks in the database."""
        from app.db.models import IngestedDocument, DocumentChunk
        from app.db.session import async_session

        async with async_session() as db:
            doc = IngestedDocument(
                organisation_id=organisation_id,
                session_id=session_id,
                original_filename=os.path.basename(file_path),
                file_path=file_path,
                file_size_bytes=os.path.getsize(file_path) if os.path.exists(file_path) else 0,
                chunk_count=len(chunks),
                status="completed",
            )
            db.add(doc)
            await db.flush()

            for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
                chunk_record = DocumentChunk(
                    document_id=doc.id,
                    organisation_id=organisation_id,
                    content=chunk,
                    chunk_index=i,
                    metadata={"source": os.path.basename(file_path)},
                )
                # If embedding is available, store it via raw SQL
                # (SQLAlchemy doesn't natively support pgvector column writes well)
                if embedding is not None:
                    embedding_str = str(embedding)
                    chunk_record.embedding = embedding_str

                db.add(chunk_record)

            await db.commit()
            return doc.id

    async def search_similar(
        self,
        query_embedding: list[float],
        organisation_id: uuid.UUID,
        top_k: int = 5,
    ) -> list[dict]:
        """
        Perform RAG retrieval: find the most similar document chunks
        to the given query embedding using pgvector cosine similarity.
        """
        from app.db.session import async_session
        from sqlalchemy import text

        async with async_session() as db:
            embedding_str = str(query_embedding)
            result = await db.execute(
                text("""
                    SELECT id, document_id, content, chunk_index, metadata,
                           embedding <=> :embedding AS distance
                    FROM document_chunks
                    WHERE organisation_id = :org_id
                      AND embedding IS NOT NULL
                    ORDER BY embedding <=> :embedding
                    LIMIT :limit
                """),
                {
                    "embedding": embedding_str,
                    "org_id": str(organisation_id),
                    "limit": top_k,
                },
            )

            rows = result.fetchall()
            return [
                {
                    "id": str(row[0]),
                    "document_id": str(row[1]),
                    "content": row[2],
                    "chunk_index": row[3],
                    "metadata": row[4],
                    "distance": float(row[5]),
                }
                for row in rows
            ]