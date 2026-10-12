"""
Providers that record each call as a span of the run's trace.

Wrapped once, where the run builds its providers, so every call - the loop's
own turns and every tool's - lands in the trace without any call site
knowing. Token counts reach the span from inside the provider, through
``usage.record``; the wrapper supplies the span, its timing and what was asked.
"""

from __future__ import annotations

import hashlib
import json

from app.llm.provider import LLMProvider, VisionLLMProvider
from app.observability import trace


def prompt_sha(messages: list[dict]) -> str:
    """A short, stable fingerprint of a prompt, to spot repeats and cache misses."""
    body = json.dumps(messages, sort_keys=True, default=str).encode()
    return hashlib.sha256(body).hexdigest()[:16]


def _identity(provider) -> dict:
    return {
        "provider": type(provider).__name__.removesuffix("Provider").lower(),
        "model": getattr(provider, "model", None) or getattr(provider, "deployment", None),
    }


class TracedChatProvider(LLMProvider):
    def __init__(self, inner: LLMProvider) -> None:
        self.inner = inner

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def chat(self, messages: list[dict], tools: list[dict] | None = None,
                   temperature: float = 0.7, max_tokens: int = 4096) -> dict:
        with trace.span(
            "llm", "chat",
            **_identity(self.inner),
            messages=len(messages),
            tools=len(tools or []),
            promptSha=prompt_sha(messages),
        ) as span:
            result = await self.inner.chat(
                messages, tools=tools, temperature=temperature, max_tokens=max_tokens,
            )
            if span is not None:
                calls = result.get("tool_calls") or []
                span.set(toolCalls=[c.get("function", {}).get("name") for c in calls])
            return result


class TracedVisionProvider(VisionLLMProvider):
    def __init__(self, inner: VisionLLMProvider) -> None:
        self.inner = inner

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def chat_with_images(self, prompt: str, images: list, *args, **kwargs) -> str:
        with trace.span("llm", "vision", **_identity(self.inner), images=len(images)):
            return await self.inner.chat_with_images(prompt, images, *args, **kwargs)
