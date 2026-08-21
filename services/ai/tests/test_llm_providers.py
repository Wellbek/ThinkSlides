"""Tests for the configurable provider layer and the Anthropic translation.

The translation helpers are module-level functions precisely so they can be
exercised without a client or an API key.
"""

import json

import pytest

from app.llm.catalog import PROVIDER_IDS, SEARCH_BACKEND_IDS, catalog_payload
from app.llm.provider import (
    AnthropicProvider,
    OpenAIProvider,
    ProviderCredentials,
    ZhipuProvider,
    anthropic_messages_from_openai,
    anthropic_response_to_openai,
    anthropic_tools_from_openai,
    build_chat_provider,
)


class _Block:
    """Stand-in for an SDK content block (attribute access, not dict)."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _Message:
    def __init__(self, content, stop_reason="end_turn"):
        self.content = content
        self.stop_reason = stop_reason


# ── Catalog ────────────────────────────────────────────────────────────


def test_catalog_offers_claude_glm_and_chatgpt():
    """The three providers the product promises are all selectable."""
    assert set(PROVIDER_IDS) == {"anthropic", "openai", "zhipu"}


def test_catalog_flags_the_zhipu_search_index_as_china_focused():
    """Issue #25's root cause is surfaced in the UI, not buried in a changelog."""
    zhipu = next(p for p in catalog_payload()["providers"] if p["id"] == "zhipu")

    assert zhipu["nativeWebSearch"] is True
    assert "China-focused" in zhipu["nativeWebSearchNote"]


def test_catalog_marks_which_search_backends_need_their_own_key():
    """Native search reuses the model credentials; external backends do not."""
    backends = {b["id"]: b for b in catalog_payload()["searchBackends"]}

    assert set(backends) == set(SEARCH_BACKEND_IDS)
    assert backends["native"]["requiresApiKey"] is False
    assert backends["tavily"]["requiresApiKey"] is True
    assert backends["firecrawl"]["requiresApiKey"] is True


# ── Factory ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "provider,model,expected",
    [
        ("anthropic", "claude-opus-5", AnthropicProvider),
        ("openai", "gpt-4o", OpenAIProvider),
        ("zhipu", "glm-4.7", ZhipuProvider),
    ],
)
def test_build_chat_provider_constructs_the_configured_provider(provider, model, expected):
    """Each catalog provider maps to its client class, with no global state read."""
    built = build_chat_provider(
        ProviderCredentials(provider=provider, model=model, api_key="test.key")
    )

    assert isinstance(built, expected)
    assert built.model == model


def test_build_chat_provider_does_not_share_state_between_configs():
    """Two organisations on the same provider get independent clients."""
    a = build_chat_provider(ProviderCredentials("anthropic", "claude-opus-5", "key-a"))
    b = build_chat_provider(ProviderCredentials("anthropic", "claude-sonnet-5", "key-b"))

    assert a is not b
    assert (a.model, b.model) == ("claude-opus-5", "claude-sonnet-5")


@pytest.mark.parametrize(
    "credentials",
    [
        ProviderCredentials("gemini", "gemini-pro", "key"),
        ProviderCredentials("anthropic", "claude-opus-5", ""),
        ProviderCredentials("anthropic", "", "key"),
    ],
)
def test_build_chat_provider_rejects_incomplete_config(credentials):
    """Unknown providers and missing key/model fail loudly at construction."""
    with pytest.raises(ValueError):
        build_chat_provider(credentials)


# ── Anthropic translation ──────────────────────────────────────────────


def test_tools_are_translated_to_anthropic_input_schema():
    """OpenAI's nested `function.parameters` becomes a flat `input_schema`."""
    converted = anthropic_tools_from_openai([
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search the web",
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
            },
        }
    ])

    assert converted == [{
        "name": "web_search",
        "description": "Search the web",
        "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
    }]


def test_system_messages_are_hoisted_out_of_the_conversation():
    """Including the loop's mid-conversation nudge, which is not universally supported inline."""
    system, messages = anthropic_messages_from_openai([
        {"role": "system", "content": "You are ThinkSlides."},
        {"role": "user", "content": "Make a deck."},
        {"role": "system", "content": "You have NOT completed the task."},
    ])

    assert system == "You are ThinkSlides.\n\nYou have NOT completed the task."
    assert messages == [{"role": "user", "content": "Make a deck."}]


def test_assistant_tool_calls_become_tool_use_blocks_with_parsed_input():
    """Anthropic wants a decoded object, not the JSON string OpenAI sends."""
    _, messages = anthropic_messages_from_openai([
        {"role": "user", "content": "Research mountains."},
        {
            "role": "assistant",
            "content": "Searching now.",
            "tool_calls": [{
                "id": "call_1",
                "function": {"name": "web_search", "arguments": '{"query": "tallest mountains"}'},
            }],
        },
    ])

    assert messages[1] == {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "Searching now."},
            {
                "type": "tool_use",
                "id": "call_1",
                "name": "web_search",
                "input": {"query": "tallest mountains"},
            },
        ],
    }


def test_malformed_tool_arguments_degrade_to_an_empty_input():
    """A truncated arguments string must not crash the whole turn."""
    _, messages = anthropic_messages_from_openai([
        {"role": "user", "content": "go"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "function": {"name": "t", "arguments": '{"q": '}}],
        },
    ])

    assert messages[1]["content"][0]["input"] == {}


def test_tool_results_become_user_tool_result_blocks():
    """The `role: "tool"` message has no Anthropic equivalent; it rides on a user turn."""
    _, messages = anthropic_messages_from_openai([
        {"role": "user", "content": "go"},
        {"role": "tool", "tool_call_id": "call_1", "content": '{"results": []}'},
    ])

    assert messages[1] == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": '{"results": []}'}],
    }


def test_assistant_turn_with_no_content_is_dropped():
    """Anthropic rejects an assistant message with zero content blocks."""
    _, messages = anthropic_messages_from_openai([
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": ""},
    ])

    assert messages == [{"role": "user", "content": "go"}]


def test_conversation_is_forced_to_open_on_a_user_turn():
    """The Messages API rejects a history that starts with an assistant turn."""
    _, messages = anthropic_messages_from_openai([
        {"role": "assistant", "content": "Resuming."},
    ])

    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "assistant"


def test_response_blocks_are_flattened_back_to_the_openai_shape():
    """Text joins into `content`; tool_use blocks re-serialize their input."""
    result = anthropic_response_to_openai(_Message([
        _Block(type="text", text="I will search."),
        _Block(type="tool_use", id="tu_1", name="web_search", input={"query": "everest"}),
    ]))

    assert result["message"]["content"] == "I will search."
    assert result["tool_calls"] == [{
        "id": "tu_1",
        "type": "function",
        "function": {"name": "web_search", "arguments": json.dumps({"query": "everest"})},
    }]


def test_response_parsing_accepts_dict_blocks_too():
    """Tolerates both SDK objects and plain dicts, as the Zhipu parser does."""
    result = anthropic_response_to_openai(_Message([{"type": "text", "text": "hello"}]))

    assert result["message"]["content"] == "hello"
    assert result["tool_calls"] == []


def test_anthropic_max_tokens_floor_leaves_room_for_thinking():
    """Current Claude models think by default and `max_tokens` caps thinking + reply."""
    assert AnthropicProvider.MIN_MAX_TOKENS >= 16000


# ── Parameters a model no longer accepts ───────────────────────────────


class _Rejecting:
    """A chat.completions stand-in that refuses the named parameters."""

    def __init__(self, *rejected: str) -> None:
        self.rejected = rejected
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        flat = {**kwargs, **(kwargs.get("extra_body") or {})}
        for name in self.rejected:
            if name in flat:
                raise _Rejected(name)
        return _Obj(choices=[_Obj(
            message=_Obj(content="ok", tool_calls=None), finish_reason="stop"
        )])


class _Rejected(Exception):
    def __init__(self, param: str) -> None:
        replacement = {"max_tokens": "max_completion_tokens"}.get(param)
        detail = f" Use '{replacement}' instead." if replacement else ""
        super().__init__(
            f"Error code: 400 - Unsupported parameter: '{param}' is not "
            f"supported with this model.{detail}"
        )
        self.param = param


class _Obj:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _openai_provider(model: str, completions) -> OpenAIProvider:
    provider = OpenAIProvider.__new__(OpenAIProvider)
    provider.client = _Obj(chat=_Obj(completions=completions))
    provider.model = model
    provider.endpoint = f"https://test.invalid/{model}"
    return provider


@pytest.mark.asyncio
async def test_a_model_that_wants_max_completion_tokens_gets_it():
    """The reported failure: every request 400d on 'max_tokens'.

    Nothing here knows the model's name - the rename comes out of the error
    the API itself returned, which is what keeps this working for whatever
    OpenAI ships next.
    """
    from app.llm.openai_compat import reset_cache
    reset_cache()
    completions = _Rejecting("max_tokens", "temperature")

    result = await _openai_provider("gpt-5.6-terra", completions).chat(
        [{"role": "user", "content": "hi"}]
    )

    assert result["message"]["content"] == "ok"
    accepted = completions.calls[-1]
    assert "max_tokens" not in accepted and "temperature" not in accepted
    assert accepted["extra_body"] == {"max_completion_tokens": 4096}


@pytest.mark.asyncio
async def test_a_model_that_still_takes_max_tokens_is_left_alone():
    """No extra round trip, and no rename, for a model that never complains."""
    from app.llm.openai_compat import reset_cache
    reset_cache()
    completions = _Rejecting()

    await _openai_provider("gpt-4o", completions).chat(
        [{"role": "user", "content": "hi"}]
    )

    assert len(completions.calls) == 1
    assert completions.calls[0]["max_tokens"] == 4096
    assert "extra_body" not in completions.calls[0]
