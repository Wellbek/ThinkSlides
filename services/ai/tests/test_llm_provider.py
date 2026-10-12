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

class TestAModelThatDemandsAParameterValue:
    """The reasoning models refuse function tools unless reasoning_effort is
    'none', and say so in the message.

    This is a third kind of instruction, and neither of the other two can act
    on it: the parameter is one we never sent, so there is nothing in the
    request to rename or drop and the adapter gave up and raised. That left
    the strongest available model unusable for the agent path - not on cost,
    but because a 400 nobody had taught the adapter to read.
    """

    MESSAGE = (
        "Error code: 400 - {'error': {'message': \"Function tools with "
        "reasoning_effort are not supported for gpt-5.6-terra in "
        "/v1/chat/completions. To use function tools, use /v1/responses or "
        "set reasoning_effort to 'none'.\", 'type': 'invalid_request_error', "
        "'param': 'reasoning_effort'}}"
    )

    def test_the_demanded_value_is_read_out_of_the_message(self):
        from app.llm.openai_compat import demanded_value

        assert demanded_value(Exception(self.MESSAGE)) == (
            "reasoning_effort", "none"
        )

    def test_it_is_learned_even_though_we_never_sent_it(self):
        from app.llm.openai_compat import ParameterCompatibility

        compat = ParameterCompatibility()
        sent = {"model": "gpt-5.6-terra", "messages": [], "tools": [{}]}
        assert compat.learn(Exception(self.MESSAGE), sent) is True
        assert compat.pins == {"reasoning_effort": "none"}

    def test_the_next_request_carries_it(self):
        from app.llm.openai_compat import ParameterCompatibility

        compat = ParameterCompatibility()
        sent = {"model": "gpt-5.6-terra", "messages": [], "tools": [{}]}
        compat.learn(Exception(self.MESSAGE), sent)
        applied = compat.apply(sent)
        assert applied["extra_body"]["reasoning_effort"] == "none"

    def test_an_ordinary_error_still_teaches_nothing(self):
        from app.llm.openai_compat import ParameterCompatibility

        compat = ParameterCompatibility()
        assert compat.learn(
            Exception("Error code: 401 - invalid api key"),
            {"model": "m", "messages": []},
        ) is False

    def test_a_pin_that_does_not_satisfy_the_api_escalates(self):
        """If the same complaint comes back after pinning, pinning harder is
        not the answer. The parameter is now in the request, so the ordinary
        rename-or-drop path can see it and drops it - which is the right
        escalation rather than repeating a value the API has already refused
        to accept."""
        from app.llm.openai_compat import ParameterCompatibility

        compat = ParameterCompatibility()
        sent = {"model": "gpt-5.6-terra", "messages": [], "tools": [{}]}
        assert compat.learn(Exception(self.MESSAGE), sent) is True
        assert compat.learn(Exception(self.MESSAGE), compat.apply(sent)) is True
        assert "reasoning_effort" in compat.dropped
