"""Document ingestion: parsing, chunking and embedding uploaded sources."""

from app.ingestion.worker import IngestionWorker

__all__ = ["IngestionWorker"]
