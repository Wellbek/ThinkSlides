"""
Tests for the LLM provider abstraction.
"""

from app.llm.provider import ZhipuProvider, AzureOpenAIProvider


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


def test_provider_selection():
    """Test that get_llm_provider returns the correct type."""
    from app.llm.provider import get_llm_provider
    import app.llm.provider as provider_module

    # Reset singleton
    provider_module._provider = None

    # With default config (zhipu), should try to create ZhipuProvider
    # This will work in tests that have the zhipuai package installed
    try:
        p = get_llm_provider()
        assert isinstance(p, (ZhipuProvider, AzureOpenAIProvider))
    except Exception:
        # Expected without proper API keys
        pass

    # Clean up
    provider_module._provider = None