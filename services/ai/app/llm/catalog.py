"""
Catalog of selectable LLM providers and deep-search backends.

This is the single source of truth for what the Settings UI offers and what
the settings API will accept. The frontend fetches it rather than hard-coding
its own copy, so adding a provider here makes it selectable everywhere.

Model lists are *suggestions*, not a whitelist - providers ship new model ids
faster than this file can track them, so the settings API accepts any
non-empty model string and the UI offers these as a dropdown with a
free-text escape hatch.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProviderSpec:
    """Everything the UI and the settings API need to know about a provider."""

    id: str
    label: str
    default_model: str
    suggested_models: tuple[str, ...]
    default_base_url: str
    # Whether this provider exposes its own web search, usable as the "native"
    # deep-search backend without a separate subscription.
    native_web_search: bool
    # Shown next to the "native" option so users understand what they are
    # picking. Empty when there is nothing to warn about.
    native_web_search_note: str = ""
    # Models that must be used for the provider's native search to work.
    # Empty means any model works.
    native_web_search_models: tuple[str, ...] = field(default_factory=tuple)
    # Models that cannot serve as the chat model because they reject tool
    # calling. The agent is a tool-calling loop, so one of these in the chat
    # role fails every generation with "tools is not supported in this model".
    # They are still perfectly valid as the Deep Thinking model, which only
    # ever asks them to search.
    no_tool_calling_models: tuple[str, ...] = field(default_factory=tuple)
    api_key_help: str = ""


PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        id="anthropic",
        label="Claude (Anthropic)",
        default_model="claude-opus-5",
        suggested_models=("claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"),
        default_base_url="https://api.anthropic.com",
        native_web_search=True,
        native_web_search_note=(
            "Anthropic's server-side web search. Returns international sources "
            "with quoted citations - the best default for English content."
        ),
        api_key_help="Create a key at console.anthropic.com. Starts with 'sk-ant-'.",
    ),
    ProviderSpec(
        id="openai",
        label="ChatGPT (OpenAI)",
        default_model="gpt-4o",
        suggested_models=("gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini"),
        default_base_url="https://api.openai.com/v1",
        native_web_search=True,
        native_web_search_note=(
            "OpenAI's web search is only available on the '-search-preview' "
            "models through the chat completions API. Set one of those as the "
            "Deep Thinking model below - not as the chat model, which they "
            "cannot serve because they do not support tool calling."
        ),
        native_web_search_models=("gpt-4o-search-preview", "gpt-4o-mini-search-preview"),
        no_tool_calling_models=("gpt-4o-search-preview", "gpt-4o-mini-search-preview"),
        api_key_help="Create a key at platform.openai.com. Starts with 'sk-'.",
    ),
    ProviderSpec(
        id="zhipu",
        label="GLM (Zhipu)",
        default_model="glm-4.7",
        suggested_models=("glm-4.7", "glm-4.6", "glm-4.5-flash"),
        default_base_url="https://open.bigmodel.cn/api/paas/v4",
        native_web_search=True,
        native_web_search_note=(
            "Zhipu's search index is China-focused: English factual queries "
            "mostly return Chinese-language sources. Prefer an external backend "
            "for non-Chinese content."
        ),
        api_key_help="Create a key at open.bigmodel.cn. Must be in 'id.secret' form to use web search.",
    ),
)

PROVIDERS_BY_ID: dict[str, ProviderSpec] = {p.id: p for p in PROVIDERS}

PROVIDER_IDS: tuple[str, ...] = tuple(p.id for p in PROVIDERS)


@dataclass(frozen=True)
class SearchBackendSpec:
    """A source of web results for Deep Thinking mode."""

    id: str
    label: str
    # Native search piggybacks on the deep-search model's own credentials, so
    # it needs no key of its own.
    requires_api_key: bool
    description: str
    api_key_help: str = ""


SEARCH_BACKENDS: tuple[SearchBackendSpec, ...] = (
    SearchBackendSpec(
        id="native",
        label="Model provider's own search",
        requires_api_key=False,
        description=(
            "Use the deep-search model provider's built-in web search. No extra "
            "subscription, but result quality depends on the provider's index."
        ),
    ),
    SearchBackendSpec(
        id="tavily",
        label="Tavily",
        requires_api_key=True,
        description="Search API built for LLM grounding. Western/international index.",
        api_key_help="Create a key at tavily.com. Starts with 'tvly-'.",
    ),
    SearchBackendSpec(
        id="firecrawl",
        label="Firecrawl",
        requires_api_key=True,
        description=(
            "Search plus full-page scraping, so results carry more content than "
            "a plain snippet."
        ),
        api_key_help="Create a key at firecrawl.dev. Starts with 'fc-'.",
    ),
)

SEARCH_BACKENDS_BY_ID: dict[str, SearchBackendSpec] = {b.id: b for b in SEARCH_BACKENDS}

SEARCH_BACKEND_IDS: tuple[str, ...] = tuple(b.id for b in SEARCH_BACKENDS)


def supports_tool_calling(provider: str | None, model: str | None) -> bool:
    """Whether this model can drive the agent loop.

    The loop only makes progress by calling tools, so a model that rejects the
    ``tools`` parameter cannot be the chat model - every generation would fail
    on the first request.
    """
    spec = PROVIDERS_BY_ID.get(provider or "")
    if spec is None:
        return True  # Unknown provider: assume capable rather than block.
    return (model or "") not in spec.no_tool_calling_models


def catalog_payload() -> dict:
    """Serialize the catalog for the settings API / frontend."""
    return {
        "providers": [
            {
                "id": p.id,
                "label": p.label,
                "defaultModel": p.default_model,
                "suggestedModels": list(p.suggested_models),
                "defaultBaseUrl": p.default_base_url,
                "nativeWebSearch": p.native_web_search,
                "nativeWebSearchNote": p.native_web_search_note,
                "nativeWebSearchModels": list(p.native_web_search_models),
                # The UI keeps these out of the chat model suggestions and
                # warns if one is typed in anyway.
                "noToolCallingModels": list(p.no_tool_calling_models),
                "apiKeyHelp": p.api_key_help,
            }
            for p in PROVIDERS
        ],
        "searchBackends": [
            {
                "id": b.id,
                "label": b.label,
                "requiresApiKey": b.requires_api_key,
                "description": b.description,
                "apiKeyHelp": b.api_key_help,
            }
            for b in SEARCH_BACKENDS
        ],
    }
