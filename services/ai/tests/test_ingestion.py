"""
Tests for document ingestion and chunking.
"""

from app.ingestion.chunker import chunk_text, chunk_text_simple


class TestChunkText:
    def test_empty_text(self):
        result = chunk_text("")
        assert result == []

    def test_short_text(self):
        result = chunk_text("Hello world")
        assert len(result) == 1
        assert result[0] == "Hello world"

    def test_long_text_splitting(self):
        text = "Paragraph " * 500  # ~5000 chars
        result = chunk_text(text, chunk_size=1000, overlap=100)
        assert len(result) > 1

    def test_overlap_preserved(self):
        text = "A" * 2000
        result = chunk_text_simple(text, chunk_size=1000, overlap=200)
        assert len(result) > 1


class TestChunkTextSimple:
    def test_empty_text(self):
        result = chunk_text_simple("")
        assert result == []

    def test_short_text(self):
        result = chunk_text_simple("Hello world")
        assert len(result) == 1

    def test_splits_at_sentence_boundary(self):
        text = "This is sentence one. " * 200
        result = chunk_text_simple(text, chunk_size=1000, overlap=100)
        assert len(result) > 1

    def test_no_infinite_loop_when_tail_le_overlap(self):
        """A final chunk no larger than the overlap must terminate, not hang."""
        text = "A" * 2000
        result = chunk_text_simple(text, chunk_size=1000, overlap=200)
        assert len(result) > 1
        # Every chunk is bounded by chunk_size (sentence snapping only shrinks).
        assert all(len(c) <= 1000 for c in result)


class TestChunkTextOversized:
    """A single paragraph larger than chunk_size must be hard-split."""

    def test_oversized_paragraph_is_split(self):
        text = "Paragraph " * 500  # ~5000 chars, no separator
        result = chunk_text(text, chunk_size=1000, overlap=100)
        assert len(result) > 1
        assert all(len(c) <= 1000 for c in result)

    def test_oversized_paragraph_among_normal_paragraphs(self):
        big = "X" * 2500
        text = f"Intro paragraph.\n\n{big}\n\nOutro paragraph."
        result = chunk_text(text, chunk_size=1000, overlap=100)
        # The oversized paragraph is split into bounded pieces; the small
        # paragraphs are preserved.
        assert len(result) > 1
        assert all(len(c) <= 1000 for c in result)
        assert result[-1] == "Outro paragraph."

    def test_terminates_without_overlap(self):
        """overlap=0 must still split and terminate."""
        text = "Y" * 3000
        result = chunk_text(text, chunk_size=1000, overlap=0)
        assert len(result) == 3