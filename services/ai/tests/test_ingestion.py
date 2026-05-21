"""
Tests for document ingestion and chunking.
"""

import pytest
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