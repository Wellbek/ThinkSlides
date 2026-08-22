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
from dataclasses import dataclass
from typing import Any
from app.config import settings
from app.llm.catalog import PROVIDERS_BY_ID
from app.llm.openai_compat import create_with_compatible_params
from app.llm.retry_notices import REASON_RATE_LIMIT, RetryNotice, publish_retry_notice
from app.observability.logging import get_logger
import asyncio
import inspect
import json
import re
import time

logger = get_logger(__name__)


class AINotConfiguredError(RuntimeError):
    """Raised when work needs a model the workspace has not configured.

    There used to be no such state: an organisation with nothing saved in
    Settings silently ran on whatever ``.env`` happened to hold, so a
    deployment that had once been pointed at a Zhipu key kept using it, kept
    reporting its billing failures as the run's failure, and gave the setup
    banner nothing to trigger on. Settings is now the only source of a model,
    and its absence is a first-class, named condition rather than a fallback.
    """

    def __init__(self, message: str = "") -> None:
        super().__init__(
            message
            or "No AI model is configured for this workspace. Open Settings and "
              "add a provider and API key before generating a presentation."
        )


# Rate limits are a routine, self-correcting condition - a token-per-minute
# ceiling clears within seconds - but an unretried 429 surfaces as a failed
# tool call. Mid-run that abandons a slide the agent was in the middle of
# fixing, which is how a deck ends up shipped with an unresolved defect.
#
# This is the ONLY place a 429 is retried. The agent loop's own retry wrapper
# deliberately declines rate limits (see AgentLoop._chat_with_retry) so that
# the wait the user is told about - "Retrying in 12s (2 of 6)" - is the whole
# truth rather than one of two nested budgets silently multiplying each other.
MAX_RATE_LIMIT_RETRIES = 6
MAX_RATE_LIMIT_SLEEP_S = 60.0
_RETRY_AFTER_RE = re.compile(r"try again in\s+([0-9.]+)\s*(ms|s)\b", re.IGNORECASE)


# Providers report an exhausted account with the same 429 they use for "you
# are going too fast" - Zhipu's 1113 (余额不足, "insufficient balance") and
# OpenAI's insufficient_quota both arrive that way. Waiting cannot fix either,
# so retrying just makes the run hang for two minutes before failing anyway.
_QUOTA_EXHAUSTED_MARKERS = (
    "insufficient_quota",
    "insufficient balance",
    "exceeded your current quota",
    "余额不足",
    "billing",
)


def _is_quota_exhausted(exc: Exception) -> bool:
    """True when the account is out of credit rather than out of pace."""
    text = str(exc).lower()
    return any(marker in text for marker in _QUOTA_EXHAUSTED_MARKERS)


# A request larger than the model can take arrives as a 429 with
# code=rate_limit_exceeded, indistinguishable by status from "you are going
# too fast" - but waiting cannot help, because the request exceeds the
# per-minute ceiling on its own. One observed run spent 63 seconds backing
# off six times over a request asking for 767,678 tokens against a 450,000
# limit, then told the user to try again in a few minutes.
_REQUEST_TOO_LARGE_MARKERS = (
    "request too large",
    "must be reduced",
    "maximum context length",
    "context_length_exceeded",
    "too many tokens",
)


def _is_request_too_large(exc: Exception) -> bool:
    """True when the request itself is oversized rather than too frequent."""
    text = str(exc).lower()
    return any(marker in text for marker in _REQUEST_TOO_LARGE_MARKERS)


def _is_rate_limit_error(exc: Exception) -> bool:
    """True for a provider's 'slow down' response, in any SDK's shape."""
    if _is_quota_exhausted(exc) or _is_request_too_large(exc):
        return False
    for candidate in (exc, getattr(exc, "response", None)):
        if getattr(candidate, "status_code", None) == 429:
            return True
    if getattr(exc, "code", None) == "rate_limit_exceeded":
        return True
    text = str(exc).lower()
    return "rate limit" in text or "rate_limit_exceeded" in text


def _rate_limit_delay(exc: Exception, attempt: int) -> float:
    """How long to wait, preferring the delay the provider itself asked for."""
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers:
        for key, scale in (("retry-after-ms", 0.001), ("retry-after", 1.0)):
            try:
                raw = headers.get(key)
            except AttributeError:
                raw = None
            if raw:
                try:
                    return min(float(raw) * scale, MAX_RATE_LIMIT_SLEEP_S)
                except (TypeError, ValueError):
                    pass

    match = _RETRY_AFTER_RE.search(str(exc))
    if match:
        seconds = float(match.group(1))
        if match.group(2).lower() == "ms":
            seconds /= 1000.0
        # A small cushion: the window has to have actually rolled over by the
        # time the retry lands, or it just 429s again.
        return min(seconds + 0.5, MAX_RATE_LIMIT_SLEEP_S)

    return min(2.0 ** attempt, MAX_RATE_LIMIT_SLEEP_S)


# The provider SDKs retry 429s internally by default. That happens *below*
# _with_rate_limit_retry, so the exception never reaches it: no rate_limit
# event is published, nothing counts down, and the interface shows a step
# sitting there for half a minute looking like a hang. A live run logged
# "Retrying request to /chat/completions in 24.886000 seconds" with the UI
# showing nothing at all.
#
# One retry policy, in one place. _with_rate_limit_retry already honours
# retry-after and narrates each wait, so the SDKs are told not to.
SDK_MAX_RETRIES = 0

async def _with_rate_limit_retry(call, *, provider: str, model: str):
    """
    Run ``call``, retrying while the provider reports a rate limit.

    Accepts either an async or a sync callable - the Zhipu SDK is synchronous
    while the OpenAI/Anthropic ones are not, and both need the same treatment.
    """
    for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
        try:
            result = call()
            if inspect.isawaitable(result):
                result = await result
            return result
        except Exception as exc:
            if attempt >= MAX_RATE_LIMIT_RETRIES or not _is_rate_limit_error(exc):
                raise
            delay = _rate_limit_delay(exc, attempt)
            logger.warning(
                "llm_rate_limited_retrying",
                provider=provider,
                model=model,
                attempt=attempt + 1,
                max_attempts=MAX_RATE_LIMIT_RETRIES,
                sleeping_s=round(delay, 2),
            )
            # Narrate the wait while it is happening - an unannounced 30s
            # pause mid-generation is indistinguishable from a hang.
            publish_retry_notice(
                RetryNotice(
                    reason=REASON_RATE_LIMIT,
                    attempt=attempt + 1,
                    max_attempts=MAX_RATE_LIMIT_RETRIES,
                    delay_seconds=delay,
                    provider=provider,
                    model=model,
                )
            )
            await asyncio.sleep(delay)


@dataclass(frozen=True)
class ProviderCredentials:
    """A fully-resolved provider configuration.

    Built either from an organisation's saved settings or from environment
    defaults, then handed to :func:`build_chat_provider`. Keeping this explicit
    is what lets one process serve several organisations with different
    providers - unlike the module-level singletons below, which exist only for
    the env-configured legacy call sites.
    """

    provider: str
    model: str
    api_key: str
    base_url: str | None = None


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

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        from zhipuai import ZhipuAI
        kwargs: dict[str, Any] = {"api_key": api_key or settings.zhipu_api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = ZhipuAI(**kwargs)
        self.model = model or settings.zhipu_model

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
            # The zhipuai SDK is synchronous, so calling it directly parks the
            # uvicorn worker's event loop for the whole request - tens of
            # seconds for a tool-calling turn. With --workers 2 that is two
            # concurrent generations away from stalling the entire service,
            # including health checks and SSE delivery for every other session.
            # Hand it to a thread, exactly as ZhipuVisionProvider does.
            # The future is built inside the lambda so each retry gets a fresh
            # one rather than re-awaiting a future that already failed.
            loop = asyncio.get_running_loop()
            response = await _with_rate_limit_retry(
                lambda: loop.run_in_executor(
                    None,
                    lambda: self.client.chat.completions.create(**kwargs),
                ),
                provider="zhipu",
                model=self.model,
            )
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
            max_retries=SDK_MAX_RETRIES,
        )
        self.deployment = settings.azure_openai_deployment
        self.endpoint = settings.azure_openai_endpoint or "azure"

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
            # A deployment fronting a current model rejects the same
            # parameters the model does - see OpenAIProvider.chat.
            response = await create_with_compatible_params(
                lambda payload: _with_rate_limit_retry(
                    lambda: self.client.chat.completions.create(**payload),
                    provider="azure_openai",
                    model=self.deployment,
                ),
                kwargs,
                endpoint=self.endpoint,
                provider="azure_openai",
                model=self.deployment,
            )
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


class OpenAIProvider(LLMProvider):
    """OpenAI (ChatGPT) provider using the openai SDK.

    Also serves any OpenAI-compatible endpoint via ``base_url``.
    """

    # Where a compatibility lesson belongs when no base_url was given. The
    # same model id can behave differently behind a proxy, so what is learned
    # is keyed on the endpoint as well as the model.
    DEFAULT_ENDPOINT = "https://api.openai.com/v1"

    def __init__(self, model: str, api_key: str, base_url: str | None = None) -> None:
        from openai import AsyncOpenAI
        kwargs: dict[str, Any] = {"api_key": api_key, "max_retries": SDK_MAX_RETRIES}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = AsyncOpenAI(**kwargs)
        self.model = model
        self.endpoint = base_url or self.DEFAULT_ENDPOINT

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> dict:
        start_time = time.time()

        # Sent as written. Which of these the model still accepts is not
        # knowable from its name - current models want max_completion_tokens
        # and reject temperature, older ones take both - so the request is
        # rewritten from what the API says when it rejects one, and the
        # lesson is remembered for the rest of the process. See openai_compat.
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if tools:
            kwargs["tools"] = tools

        try:
            response = await create_with_compatible_params(
                lambda payload: _with_rate_limit_retry(
                    lambda: self.client.chat.completions.create(**payload),
                    provider="openai",
                    model=self.model,
                ),
                kwargs,
                endpoint=self.endpoint,
                provider="openai",
                model=self.model,
            )
        except Exception as e:
            logger.error("openai_api_error", error=str(e), model=self.model)
            raise

        result = _parse_openai_choice(response)
        logger.info(
            "llm_call_completed",
            provider="openai",
            model=self.model,
            duration_ms=int((time.time() - start_time) * 1000),
            has_tool_calls=len(result["tool_calls"]) > 0,
        )
        return result


class AnthropicProvider(LLMProvider):
    """Claude provider using the anthropic SDK.

    Anthropic's Messages API differs from the OpenAI shape in three ways that
    this class hides from the agent loop:

    - The system prompt is a top-level argument, not a message.
    - Tools are ``{name, description, input_schema}``, and calls/results are
      ``tool_use`` / ``tool_result`` content blocks rather than a ``tool_calls``
      array and ``role: "tool"`` messages.
    - Sampling parameters (``temperature``, ``top_p``, ``top_k``) are rejected
      by current models, so none are sent.
    """

    # Current Claude models think by default, and `max_tokens` caps thinking
    # plus the reply together. A 4k budget - fine for a non-thinking provider -
    # can truncate mid-answer, so the floor is raised. This stays under the
    # ~16k mark where non-streaming requests risk an HTTP timeout.
    MIN_MAX_TOKENS = 16000

    def __init__(self, model: str, api_key: str, base_url: str | None = None) -> None:
        from anthropic import AsyncAnthropic
        kwargs: dict[str, Any] = {"api_key": api_key, "max_retries": SDK_MAX_RETRIES}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = AsyncAnthropic(**kwargs)
        self.model = model

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> dict:
        start_time = time.time()

        system, converted = anthropic_messages_from_openai(messages)

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": converted,
            "max_tokens": max(max_tokens, self.MIN_MAX_TOKENS),
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = anthropic_tools_from_openai(tools)

        try:
            response = await _with_rate_limit_retry(
                lambda: self.client.messages.create(**kwargs),
                provider="anthropic",
                model=self.model,
            )
        except Exception as e:
            logger.error("anthropic_api_error", error=str(e), model=self.model)
            raise

        duration_ms = int((time.time() - start_time) * 1000)

        if getattr(response, "stop_reason", None) == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            logger.warning("anthropic_refusal", model=self.model, category=category)
            return {
                "message": {
                    "role": "assistant",
                    "content": (
                        "The model declined this request"
                        + (f" ({category})" if category else "")
                        + "."
                    ),
                },
                "tool_calls": [],
            }

        result = anthropic_response_to_openai(response)
        logger.info(
            "llm_call_completed",
            provider="anthropic",
            model=self.model,
            duration_ms=duration_ms,
            has_tool_calls=len(result["tool_calls"]) > 0,
            stop_reason=getattr(response, "stop_reason", None),
        )
        return result


# ── OpenAI <-> Anthropic translation helpers ───────────────────────────
#
# Module-level (not methods) so they can be unit-tested without constructing a
# client or holding an API key.


def _parse_openai_choice(response) -> dict:
    """Normalize an OpenAI-shaped chat completion into the provider contract."""
    choice = response.choices[0] if response.choices else None
    if not choice:
        return {"message": {"role": "assistant", "content": ""}, "tool_calls": []}

    assistant_message = choice.message
    result: dict[str, Any] = {
        "message": {"role": "assistant", "content": assistant_message.content or ""},
        "tool_calls": [],
    }
    for tc in getattr(assistant_message, "tool_calls", None) or []:
        result["tool_calls"].append({
            "id": tc.id,
            "type": "function",
            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
        })
    return result


def anthropic_tools_from_openai(tools: list[dict]) -> list[dict]:
    """Convert OpenAI function-calling schemas to Anthropic tool definitions."""
    converted: list[dict] = []
    for tool in tools:
        fn = tool.get("function", tool)
        converted.append({
            "name": fn.get("name", ""),
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
        })
    return converted


def anthropic_messages_from_openai(messages: list[dict]) -> tuple[str, list[dict]]:
    """Split OpenAI-shaped messages into (system prompt, Anthropic messages).

    Every system message is folded into the top-level system prompt. The agent
    loop injects a mid-conversation system message to push the agent to keep
    going; hoisting it to the top rather than sending it inline keeps this
    working on every Claude model (mid-conversation system messages are not
    supported across the whole family).

    Consecutive same-role messages are left as-is - the Messages API merges
    them into one turn.
    """
    system_parts: list[str] = []
    converted: list[dict] = []

    for msg in messages:
        role = msg.get("role")
        content = msg.get("content") or ""

        if role == "system":
            if content:
                system_parts.append(content)
            continue

        if role == "tool":
            converted.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": msg.get("tool_call_id", ""),
                    "content": content if isinstance(content, str) else json.dumps(content),
                }],
            })
            continue

        if role == "assistant":
            blocks: list[dict] = []
            if content:
                blocks.append({"type": "text", "text": content})
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function", {})
                raw_args = fn.get("arguments") or "{}"
                try:
                    parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except json.JSONDecodeError:
                    parsed_args = {}
                blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id", ""),
                    "name": fn.get("name", ""),
                    "input": parsed_args,
                })
            # Anthropic rejects an assistant turn with no content blocks.
            if blocks:
                converted.append({"role": "assistant", "content": blocks})
            continue

        converted.append({"role": "user", "content": content})

    # The Messages API requires the conversation to open on a user turn.
    if converted and converted[0]["role"] != "user":
        converted.insert(0, {"role": "user", "content": "Begin."})

    return "\n\n".join(system_parts), converted


def anthropic_response_to_openai(response) -> dict:
    """Convert an Anthropic Message into the provider contract shape."""
    text_parts: list[str] = []
    tool_calls: list[dict] = []

    for block in getattr(response, "content", None) or []:
        block_type = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
        if block_type == "text":
            text = block.get("text") if isinstance(block, dict) else getattr(block, "text", "")
            if text:
                text_parts.append(text)
        elif block_type == "tool_use":
            if isinstance(block, dict):
                block_id, name, tool_input = block.get("id"), block.get("name"), block.get("input")
            else:
                block_id, name, tool_input = block.id, block.name, block.input
            tool_calls.append({
                "id": block_id or "",
                "type": "function",
                "function": {"name": name or "", "arguments": json.dumps(tool_input or {})},
            })

    return {
        "message": {"role": "assistant", "content": "\n".join(text_parts)},
        "tool_calls": tool_calls,
    }


def build_chat_provider(credentials: ProviderCredentials) -> LLMProvider:
    """Construct a provider from an explicit config, bypassing the singletons.

    Raises:
        ValueError: If the provider id is unknown or the credentials are empty.
    """
    if credentials.provider not in PROVIDERS_BY_ID:
        raise ValueError(f"Unknown LLM provider '{credentials.provider}'")
    if not credentials.api_key:
        raise ValueError(f"No API key configured for provider '{credentials.provider}'")
    if not credentials.model:
        raise ValueError(f"No model configured for provider '{credentials.provider}'")

    if credentials.provider == "anthropic":
        return AnthropicProvider(
            model=credentials.model,
            api_key=credentials.api_key,
            base_url=credentials.base_url,
        )
    if credentials.provider == "openai":
        return OpenAIProvider(
            model=credentials.model,
            api_key=credentials.api_key,
            base_url=credentials.base_url,
        )
    return ZhipuProvider(
        model=credentials.model,
        api_key=credentials.api_key,
        base_url=credentials.base_url,
    )


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

    def __init__(self, model: str, api_key: str, base_url: str | None = None) -> None:
        from zhipuai import ZhipuAI
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = ZhipuAI(**kwargs)
        self.model = model

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
            # to avoid blocking the event loop during long vision LLM calls.
            # Retried on a rate limit like every other call: a 429 here fails
            # template extraction, which loses the user's brand for the run.
            loop = asyncio.get_running_loop()
            response = await _with_rate_limit_retry(
                lambda: loop.run_in_executor(
                    None,
                    lambda: self.client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    ),
                ),
                provider="zhipu",
                model=self.model,
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


class OpenAIVisionProvider(VisionLLMProvider):
    """OpenAI vision provider using the openai SDK.

    Also serves any OpenAI-compatible endpoint via ``base_url``, same as
    ``OpenAIProvider``. Constructed from explicit credentials rather than from
    the environment, because which model looks at an image is the user's
    choice in settings like every other model in the product.
    """

    DEFAULT_ENDPOINT = OpenAIProvider.DEFAULT_ENDPOINT

    def __init__(self, model: str, api_key: str, base_url: str | None = None) -> None:
        from openai import AsyncOpenAI
        kwargs: dict[str, Any] = {"api_key": api_key, "max_retries": SDK_MAX_RETRIES}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = AsyncOpenAI(**kwargs)
        self.model = model
        self.endpoint = base_url or self.DEFAULT_ENDPOINT

    async def chat_with_images(
        self,
        prompt: str,
        images: list[str],
        temperature: float = 0.4,
        max_tokens: int = 4096,
    ) -> str:
        start_time = time.time()

        content: list[dict] = [{"type": "text", "text": prompt}]
        for img in images:
            url = img if img.startswith("data:") else f"data:image/png;base64,{img}"
            content.append({"type": "image_url", "image_url": {"url": url}})

        # Same compatibility dance as the text path: current models want
        # max_completion_tokens and reject temperature, older ones take both,
        # and the model's name does not tell you which. See openai_compat.
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": content}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        try:
            response = await create_with_compatible_params(
                lambda payload: _with_rate_limit_retry(
                    lambda: self.client.chat.completions.create(**payload),
                    provider="openai",
                    model=self.model,
                ),
                kwargs,
                endpoint=self.endpoint,
                provider="openai",
                model=self.model,
            )
        except Exception as e:
            logger.error("openai_vision_api_error", error=str(e), model=self.model)
            raise

        choice = response.choices[0] if response.choices else None
        text = (choice.message.content if choice and choice.message else "") or ""
        logger.info(
            "vision_llm_call_completed",
            provider="openai",
            model=self.model,
            image_count=len(images),
            duration_ms=int((time.time() - start_time) * 1000),
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
            max_retries=SDK_MAX_RETRIES,
        )
        self.deployment = settings.azure_openai_vision_deployment
        self.endpoint = settings.azure_openai_endpoint or "azure"

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
            # Vision deployments retire parameters on the same schedule as
            # text ones - see OpenAIProvider.chat.
            response = await create_with_compatible_params(
                lambda payload: _with_rate_limit_retry(
                    lambda: self.client.chat.completions.create(**payload),
                    provider="azure_openai",
                    model=self.deployment,
                ),
                {
                    "model": self.deployment,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
                endpoint=self.endpoint,
                provider="azure_openai",
                model=self.deployment,
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

# There are deliberately no process-wide provider singletons here.
#
# There were three - get_llm_provider, get_tools_llm_provider and
# get_vision_llm_provider - each built from environment variables. What they
# produced was a workspace that looked configured without ever having been:
# generations ran on whatever key .env held, and when that key was exhausted
# the failure named a provider the user had never chosen. Every provider is
# now constructed from the organisation's stored settings through
# build_chat_provider / build_vision_provider and published on the run
# context, so a workspace with nothing configured says so instead of quietly
# borrowing somebody else's key.


def build_vision_provider(credentials: ProviderCredentials) -> VisionLLMProvider:
    """Construct a vision provider from explicit config, bypassing the singletons.

    The counterpart of ``build_chat_provider``, and it exists for the same
    reason: which model runs is the user's choice in settings, not whatever
    the process happened to be started with. A harness that reached for the
    environment singleton instead ran on a provider the user had never picked,
    and failed naming it.

    Raises:
        ValueError: If the provider id is unknown or the credentials are empty.
    """
    if credentials.provider not in PROVIDERS_BY_ID:
        raise ValueError(f"Unknown LLM provider '{credentials.provider}'")
    if not credentials.api_key:
        raise ValueError(f"No API key configured for provider '{credentials.provider}'")
    if not credentials.model:
        raise ValueError(f"No model configured for provider '{credentials.provider}'")

    if credentials.provider == "openai":
        return OpenAIVisionProvider(
            model=credentials.model,
            api_key=credentials.api_key,
            base_url=credentials.base_url,
        )
    if credentials.provider == "zhipu":
        return ZhipuVisionProvider(
            model=credentials.model,
            api_key=credentials.api_key,
            base_url=credentials.base_url,
        )
    raise ValueError(
        f"'{credentials.provider}' has no vision support in this build"
    )
