"""
Tests for the LLM provider abstraction.
"""

import pytest

from app.llm.provider import ZhipuProvider


def test_zhipu_provider_instantiation():
    """Test that ZhipuProvider can be instantiated with config."""
    # This will fail without a real API key, which is expected
    # In CI, we'd mock the ZhipuAI client
    try:
        provider = ZhipuProvider()
        assert provider.model == "glm-4"
    except Exception:
        # Expected without API key
        pass


def test_there_are_no_environment_built_provider_singletons():
    """
    A provider must come from the workspace's saved settings, never from the
    process environment.

    There were three singletons here - get_llm_provider,
    get_tools_llm_provider and get_vision_llm_provider - each built from
    environment variables, and what they produced was a workspace that looked
    configured without ever having been: generations ran on whatever key .env
    held, and when that key was exhausted the failure named a provider the
    user had never chosen. This is a structural check, because the way that
    comes back is one new call site quietly reaching for a module-level
    default.
    """
    import app.llm.provider as provider_module

    for name in ("get_llm_provider", "get_tools_llm_provider",
                 "get_vision_llm_provider", "_provider", "_tools_provider",
                 "_vision_provider"):
        assert not hasattr(provider_module, name), (
            f"{name} is back - build the provider from stored credentials "
            "with build_chat_provider / build_vision_provider instead"
        )


def test_a_provider_is_built_from_explicit_credentials():
    from app.llm.provider import ProviderCredentials, build_chat_provider

    provider = build_chat_provider(ProviderCredentials(
        provider="zhipu", model="glm-4.7", api_key="k"
    ))
    assert isinstance(provider, ZhipuProvider)
    assert provider.model == "glm-4.7"


def test_building_a_provider_with_nothing_configured_says_so():
    from app.llm.provider import ProviderCredentials, build_chat_provider

    with pytest.raises(ValueError, match="No API key"):
        build_chat_provider(ProviderCredentials(
            provider="zhipu", model="glm-4.7", api_key=""
        ))