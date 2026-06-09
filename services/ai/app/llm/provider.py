"""
LLM Provider Abstraction

Provides a unified interface for LLM chat completions that defaults to
Zhipu GLM (zhipuai SDK, model glm-4) but is fully compatible with
Azure OpenAI by swapping base_url and api_key via environment variables.

The provider selection is controlled by the LLM_PROVIDER env var:
  - "zhipu" (default): Uses zhipuai SDK
  - "azure_openai": Uses openai SDK with Azure endpoint

Both providers expose the same chat() interface with tool/function calling support.

A separate VisionLLMProvider is available for multimodal (image) inputs,
used by the template extraction pipeline to analyze PDF-based templates
through vision models like GLM-4.6V.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Optional
from app.config import settings
from app.observability.logging import get_logger
import asyncio
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


class VisionLLMProvider:
    """
    Vision-capable LLM provider for analysing images (e.g. PDF page renders).

    Wraps the same SDK clients as the text-only providers but exposes a
    ``chat_with_images`` method that accepts base64-encoded images alongside
    the text prompt.
    """

    async def chat_with_images(
        self,
        prompt: str,
        images: list[str],
        temperature: float = 0.4,
        max_tokens: int = 4096,
    ) -> str:
        """
        Send a multimodal chat request with text + images.

        Args:
            prompt: The text instruction / question.
            images: List of base64-encoded image strings or full data URI strings
                    (e.g. ``"data:image/jpeg;base64,...."``).  Plain base64 strings
                    are assumed to be PNG.
            temperature: Sampling temperature (lower for more deterministic output).
            max_tokens: Maximum tokens to generate.

        Returns:
            The assistant's text response.
        """
        ...


class ZhipuVisionProvider(VisionLLMProvider):
    """Zhipu GLM-4.6V vision provider using the zhipuai SDK."""

    def __init__(self) -> None:
        from zhipuai import ZhipuAI
        self.client = ZhipuAI(api_key=settings.zhipu_api_key)
        self.model = settings.zhipu_vision_model

    async def chat_with_images(
        self,
        prompt: str,
        images: list[str],
        temperature: float = 0.4,
        max_tokens: int = 4096,
    ) -> str:
        start_time = time.time()

        # Build the multimodal content array
        # Images can be plain base64 strings (assumed PNG) or full data URIs
        content: list[dict] = [{"type": "text", "text": prompt}]
        for img in images:
            url = img if img.startswith("data:") else f"data:image/png;base64,{img}"
            content.append({
                "type": "image_url",
                "image_url": {"url": url},
            })

        messages = [{"role": "user", "content": content}]

        try:
            # Run the synchronous ZhipuAI SDK call in a thread pool
            # to avoid blocking the event loop during long vision LLM calls
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                ),
            )
        except Exception as e:
            logger.error("zhipu_vision_api_error", error=str(e), model=self.model)
            raise

        duration_ms = int((time.time() - start_time) * 1000)
        choice = response.choices[0] if response.choices else None
        text = choice.message.content if choice and choice.message else ""

        logger.info(
            "vision_llm_call_completed",
            provider="zhipu",
            model=self.model,
            image_count=len(images),
            duration_ms=duration_ms,
        )
        return text


class AzureOpenAIVisionProvider(VisionLLMProvider):
    """Azure OpenAI vision provider (GPT-4o / GPT-4V) using the openai SDK."""

    def __init__(self) -> None:
        from openai import AsyncAzureOpenAI
        self.client = AsyncAzureOpenAI(
            api_key=settings.azure_openai_api_key,
            azure_endpoint=settings.azure_openai_endpoint or "",
            api_version=settings.azure_openai_api_version,
        )
        self.deployment = settings.azure_openai_vision_deployment

    async def chat_with_images(
        self,
        prompt: str,
        images: list[str],
        temperature: float = 0.4,
        max_tokens: int = 4096,
    ) -> str:
        start_time = time.time()

        # Images can be plain base64 strings (assumed PNG) or full data URIs
        content: list[dict] = [{"type": "text", "text": prompt}]
        for img in images:
            url = img if img.startswith("data:") else f"data:image/png;base64,{img}"
            content.append({
                "type": "image_url",
                "image_url": {"url": url},
            })

        messages = [{"role": "user", "content": content}]

        try:
            response = await self.client.chat.completions.create(
                model=self.deployment,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as e:
            logger.error("azure_vision_api_error", error=str(e), deployment=self.deployment)
            raise

        duration_ms = int((time.time() - start_time) * 1000)
        choice = response.choices[0] if response.choices else None
        text = choice.message.content if choice and choice.message else ""

        logger.info(
            "vision_llm_call_completed",
            provider="azure_openai",
            deployment=self.deployment,
            image_count=len(images),
            duration_ms=duration_ms,
        )
        return text


# ── Singleton instances ────────────────────────────────────────────────

_provider: LLMProvider | None = None
_vision_provider: VisionLLMProvider | None = None


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


def get_vision_llm_provider() -> VisionLLMProvider:
    """Get or create the vision-capable LLM provider based on configuration."""
    global _vision_provider
    if _vision_provider is None:
        if settings.vision_llm_provider == "azure_openai":
            _vision_provider = AzureOpenAIVisionProvider()
            logger.info("vision_llm_provider_initialized", provider="azure_openai")
        else:
            _vision_provider = ZhipuVisionProvider()
            logger.info("vision_llm_provider_initialized", provider="zhipu", model=settings.zhipu_vision_model)
    return _vision_provider
