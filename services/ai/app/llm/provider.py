"""
LLM Provider Abstraction

Provides a unified interface for LLM chat completions that defaults to
Zhipu GLM (zhipuai SDK, model glm-4) but is fully compatible with
Azure OpenAI by swapping base_url and api_key via environment variables.

The provider selection is controlled by the LLM_PROVIDER env var:
  - "zhipu" (default): Uses zhipuai SDK
  - "azure_openai": Uses openai SDK with Azure endpoint

Both providers expose the same chat() interface with tool/function calling support.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Optional
from app.config import settings
from app.observability.logging import get_logger
import json
import time

logger = get_logger(__name__)


class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    @abstractmethod
    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> dict:
        """
        Send a chat completion request.

        Args:
            messages: List of message dicts with 'role' and 'content'
            tools: Optional list of tool schemas in OpenAI function-calling format
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate

        Returns:
            Dict with 'message' (assistant message) and 'tool_calls' (list of tool calls)
        """
        ...


class ZhipuProvider(LLMProvider):
    """Zhipu GLM provider using the zhipuai SDK."""

    def __init__(self) -> None:
        from zhipuai import ZhipuAI
        self.client = ZhipuAI(api_key=settings.zhipu_api_key)
        self.model = settings.zhipu_model

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> dict:
        start_time = time.time()

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if tools:
            kwargs["tools"] = tools

        try:
            response = self.client.chat.completions.create(**kwargs)
        except Exception as e:
            logger.error("zhipu_api_error", error=str(e), model=self.model)
            raise

        duration_ms = int((time.time() - start_time) * 1000)
        choice = response.choices[0] if response.choices else None

        if not choice:
            return {"message": {"role": "assistant", "content": ""}, "tool_calls": []}

        assistant_message = choice.message
        result: dict[str, Any] = {
            "message": {
                "role": "assistant",
                "content": assistant_message.content or "",
            },
            "tool_calls": [],
        }

        # Parse tool calls if present
        if hasattr(assistant_message, "tool_calls") and assistant_message.tool_calls:
            for tc in assistant_message.tool_calls:
                result["tool_calls"].append({
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                })

        logger.info(
            "llm_call_completed",
            provider="zhipu",
            model=self.model,
            duration_ms=duration_ms,
            has_tool_calls=len(result["tool_calls"]) > 0,
            finish_reason=choice.finish_reason if choice else None,
        )

        return result


class AzureOpenAIProvider(LLMProvider):
    """Azure OpenAI provider using the openai SDK."""

    def __init__(self) -> None:
        from openai import AsyncAzureOpenAI
        self.client = AsyncAzureOpenAI(
            api_key=settings.azure_openai_api_key,
            azure_endpoint=settings.azure_openai_endpoint or "",
            api_version=settings.azure_openai_api_version,
        )
        self.deployment = settings.azure_openai_deployment

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> dict:
        start_time = time.time()

        kwargs: dict[str, Any] = {
            "model": self.deployment,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if tools:
            kwargs["tools"] = tools

        try:
            response = await self.client.chat.completions.create(**kwargs)
        except Exception as e:
            logger.error("azure_openai_api_error", error=str(e), deployment=self.deployment)
            raise

        duration_ms = int((time.time() - start_time) * 1000)
        choice = response.choices[0] if response.choices else None

        if not choice:
            return {"message": {"role": "assistant", "content": ""}, "tool_calls": []}

        assistant_message = choice.message
        result: dict[str, Any] = {
            "message": {
                "role": "assistant",
                "content": assistant_message.content or "",
            },
            "tool_calls": [],
        }

        if assistant_message.tool_calls:
            for tc in assistant_message.tool_calls:
                result["tool_calls"].append({
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                })

        logger.info(
            "llm_call_completed",
            provider="azure_openai",
            deployment=self.deployment,
            duration_ms=duration_ms,
            has_tool_calls=len(result["tool_calls"]) > 0,
        )

        return result


# Singleton provider instance
_provider: LLMProvider | None = None


def get_llm_provider() -> LLMProvider:
    """Get or create the LLM provider based on configuration."""
    global _provider
    if _provider is None:
        if settings.llm_provider == "azure_openai":
            _provider = AzureOpenAIProvider()
            logger.info("llm_provider_initialized", provider="azure_openai")
        else:
            _provider = ZhipuProvider()
            logger.info("llm_provider_initialized", provider="zhipu", model=settings.zhipu_model)
    return _provider