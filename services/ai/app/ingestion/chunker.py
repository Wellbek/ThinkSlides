"""
Text chunking strategies for document ingestion.

Provides configurable chunking with overlap for RAG retrieval.
"""

from __future__ import annotations
from typing import Optional


def chunk_text(
    text: str,
    chunk_size: int = 1500,
    overlap: int = 200,
    separator: str = "\n\n",
) -> list[str]:
    """
    Split text into overlapping chunks.

    Strategy:
    1. Split by separator (paragraph breaks)
    2. Group paragraphs into chunks up to chunk_size
    3. Add overlap from previous chunk

    Args:
        text: The full text to chunk
        chunk_size: Maximum characters per chunk
        overlap: Number of characters to overlap between chunks
        separator: Paragraph separator

    Returns:
        List of text chunks
    """
    if not text or not text.strip():
        return []

    if len(text) <= chunk_size:
        return [text]

    # Split into paragraphs
    paragraphs = text.split(separator)
    chunks: list[str] = []
    current_chunk: list[str] = []
    current_length = 0

    for para in paragraphs:
        para_length = len(para)

        # If adding this paragraph exceeds the limit, finalize the current chunk
        if current_length + para_length + len(separator) > chunk_size and current_chunk:
            chunk_text_str = separator.join(current_chunk)
            chunks.append(chunk_text_str)

            # Start new chunk with overlap
            # Find paragraphs from the end that fit within overlap
            overlap_text = chunk_text_str[-overlap:] if overlap > 0 else ""
            current_chunk = []
            current_length = 0

            # If we have overlap text, include it
            if overlap_text:
                current_chunk.append(overlap_text)
                current_length = len(overlap_text)

        current_chunk.append(para)
        current_length += para_length + len(separator)

    # Add remaining chunk
    if current_chunk:
        chunks.append(separator.join(current_chunk))

    return chunks


def chunk_text_simple(
    text: str,
    chunk_size: int = 1500,
    overlap: int = 200,
) -> list[str]:
    """
    Simple fixed-size chunking with overlap.
    Falls back to character-level splitting when paragraph boundaries don't align.
    """
    if not text or not text.strip():
        return []

    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0

    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]

        # Try to find a sentence boundary near the end
        if end < len(text):
            # Look for period, question mark, or exclamation near the end
            for punct in [". ", "? ", "! ", "\n"]:
                last_punct = chunk.rfind(punct)
                if last_punct > chunk_size * 0.5:
                    chunk = chunk[: last_punct + len(punct)]
                    break

        chunks.append(chunk.strip())
        start += len(chunk) - overlap

    return chunks