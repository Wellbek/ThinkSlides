"""
Text chunking strategies for document ingestion.

Provides configurable chunking with overlap for RAG retrieval.
"""

from __future__ import annotations


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

        # A paragraph larger than the chunk size cannot be kept whole - the
        # grouping logic below would just absorb it and emit one giant chunk,
        # dominating retrieval. Flush whatever is pending, then hard-split the
        # paragraph by characters (with overlap) into bounded pieces.
        if para_length > chunk_size:
            if current_chunk:
                chunks.append(separator.join(current_chunk))
                current_chunk = []
                current_length = 0
            chunks.extend(_split_oversized_paragraph(para, chunk_size, overlap))
            continue

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
        is_final = end >= len(text)

        # Try to find a sentence boundary near the end (only for non-final
        # chunks - the final chunk is the remaining tail and is kept as-is).
        if not is_final:
            # Look for period, question mark, or exclamation near the end
            for punct in [". ", "? ", "! ", "\n"]:
                last_punct = chunk.rfind(punct)
                if last_punct > chunk_size * 0.5:
                    chunk = chunk[: last_punct + len(punct)]
                    break

        chunks.append(chunk.strip())

        # The final chunk has consumed the rest of the text - stop, otherwise
        # the loop could reprocess the tail forever.
        if is_final:
            break

        # Always advance by at least 1 so a chunk no larger than the overlap
        # (e.g. the last sentence snapped short) cannot stall the loop.
        start += max(1, len(chunk) - overlap)

    return chunks


def _split_oversized_paragraph(
    text: str,
    chunk_size: int,
    overlap: int,
) -> list[str]:
    """Character-level split for a paragraph larger than ``chunk_size``.

    Used by :func:`chunk_text` when a single paragraph (no separator inside)
    exceeds the chunk size, so it is broken into bounded, overlapping pieces
    instead of being emitted as one oversized chunk.
    """
    # Advance by chunk_size minus the overlap, keeping at least a 1-char step
    # so a misconfigured overlap >= chunk_size cannot loop forever.
    step = max(1, chunk_size - overlap)
    pieces: list[str] = []
    for start in range(0, len(text), step):
        piece = text[start : start + chunk_size]
        if piece.strip():
            pieces.append(piece)
        if start + chunk_size >= len(text):
            break
    return pieces