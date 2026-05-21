"""
Embedding generation for document chunks.

Abstracts the embedding API behind a provider interface,
supporting both Zhipu and Azure OpenAI embedding models.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Optional
from app.config import settings
from app.observability.logging import get_logger

logger = get_logger(__name__)

EMBEDDING_DIMENSION = 1536


class EmbeddingProvider(ABC):
    """Abstract base class for embedding providers."""

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a list of texts."""
        ...

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Return the embedding dimension."""
        ...


class ZhipuEmbeddingProvider(EmbeddingProvider):
    """Zhipu embedding provider using embedding-2 model."""

    def __init__(self) -> None:
        from zhipuai import ZhipuAI
        self.client = ZhipuAI(api_key=settings.zhipu_api_key)

    @property
    def dimension(self) -> int:
        return 1024  # Zhipu embedding-2 dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        embeddings = []
        for text in texts:
            try:
                response = self.client.embeddings.create(
                    model="embedding-2",
                    input=text,
                )
                embedding = response.data[0].embedding
                embeddings.append(embedding)
            except Exception as e:
                logger.error("zhipu_embedding_failed", error=str(e))
                embeddings.append([0.0] * self.dimension)
        return embeddings


class AzureOpenAIEmbeddingProvider(EmbeddingProvider):
    """Azure OpenAI embedding provider using text-embedding-ada-002."""

    def __init__(self) -> None:
        from openai import AsyncAzureOpenAI
        self.client = AsyncAzureOpenAI(
            api_key=settings.azure_openai_api_key,
            azure_endpoint=settings.azure_openai_endpoint or "",
            api_version=settings.azure_openai_api_version,
        )

    @property
    def dimension(self) -> int:
        return 1536

    async def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            response = await self.client.embeddings.create(
                model="text-embedding-ada-002",
                input=texts,
            )
            return [item.embedding for item in response.data]
        except Exception as e:
            logger.error("azure_embedding_failed", error=str(e))
            return [[0.0] * self.dimension for _ in texts]


_provider: Optional[EmbeddingProvider] = None


def get_embedding_provider() -> EmbeddingProvider:
    """Get or create the embedding provider based on configuration."""
    global _provider
    if _provider is None:
        if settings.llm_provider == "azure_openai":
            _provider = AzureOpenAIEmbeddingProvider()
        else:
            _provider = ZhipuEmbeddingProvider()
        logger.info("embedding_provider_initialized", provider=type(_provider).__name__)
    return _provider