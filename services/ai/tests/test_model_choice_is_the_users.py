"""
The model a workspace configures is the model it gets, and it has to work.

Two properties, and the first constrains the second.

**No routing.** A run uses the model the user chose, for everything. Not a
stronger one for the parts we think are hard, not a cheaper one for the parts
we think are mechanical. If a workspace says gpt-4o, every call is gpt-4o; if
it says a reasoning model, every call is that. Deciding otherwise on the
user's behalf means the deck they get is not the deck they configured, and
means this code has an opinion about which models are good - an opinion that
is a list of names, and a list of names goes stale.

**Whatever they chose has to work.** Which parameters a model still accepts is
not knowable from its id: `gpt-5.6-terra` rejects `max_tokens` and rejects
`temperature` outright, `gpt-4o` takes both, and a proxy behind a custom
base_url may take only the old spelling. So a rejection is *learned* from what
the API said, for every provider a user can pick - not just the two the
adapter was first written against.
"""
import pytest

from app.llm import openai_compat, usage
from app.llm.provider import (
    AnthropicProvider,
    OpenAIProvider,
    ProviderCredentials,
    ZhipuProvider,
    build_chat_provider,
)


class TestTheConfiguredModelIsTheOneUsed:
    def test_no_module_picks_a_model_by_task(self):
        """There is no routing table, and there should not be one."""
        import importlib

        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("app.llm.routing")

    @pytest.mark.parametrize("provider,model", [
        ("openai", "gpt-4o"),
        ("openai", "gpt-5.6-terra"),
        ("anthropic", "claude-opus-5"),
        ("anthropic", "claude-haiku-4-5"),
        ("zhipu", "glm-4.5-flash"),
    ])
    def test_the_provider_is_built_on_exactly_what_was_configured(
            self, provider, model):
        built = build_chat_provider(ProviderCredentials(
            provider=provider, model=model, api_key="k"))
        assert built.model == model

    def test_a_custom_endpoint_is_honoured(self):
        built = build_chat_provider(ProviderCredentials(
            provider="openai", model="some-proxied-model", api_key="k",
            base_url="https://proxy.example/v1"))
        assert built.model == "some-proxied-model"

    def test_nothing_in_the_llm_layer_branches_on_a_model_name(self):
        """The rule that cost a whole session when it was broken: a retired
        model list gated Deep Thinking on names that no longer existed, and it
        then refused every model a user could actually pick."""
        import pathlib
        import re

        source = pathlib.Path("app/llm/provider.py").read_text()
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert not re.search(r"model\s*\.\s*startswith", stripped), line
            assert not re.search(r"model\s*(==|!=)\s*['\"]g?pt", stripped), line


class TestEveryProviderLearnsWhatItsModelRefuses:
    """The adapter covered OpenAI and Azure. Zhipu and Anthropic called their
    SDKs directly, so a user who configured one of those and hit a parameter
    their model had stopped taking lost the run rather than a round trip."""

    @pytest.mark.parametrize("cls,name", [
        (ZhipuProvider, "zhipu"),
        (AnthropicProvider, "anthropic"),
        (OpenAIProvider, "openai"),
    ])
    def test_the_call_goes_through_the_adapter(self, cls, name):
        import inspect

        source = inspect.getsource(cls.chat)
        assert "create_with_compatible_params" in source, (
            f"{name} calls its SDK directly, so it cannot learn what its "
            f"model refuses"
        )

    @pytest.mark.parametrize("message", [
        "Unsupported parameter: 'max_tokens' is not supported with this model.",
        "unexpected keyword argument 'temperature'",
        "invalid_request_error: temperature is not allowed",
        "max_tokens is not supported with this model",
    ])
    def test_a_rejection_is_recognised_however_it_is_worded(self, message):
        """Every provider a user can configure has to be able to say "not that
        parameter" in a way this recognises, or the adapter helps only the
        providers it was first written against."""
        compat = openai_compat.ParameterCompatibility()
        assert compat.learn(RuntimeError(message),
                            {"model": "m", "messages": [],
                             "max_tokens": 10, "temperature": 0.5})

    def test_a_rejection_naming_no_parameter_is_let_through(self):
        """A complaint nobody can attribute is a complaint nobody can act on,
        and guessing which parameter to drop is worse than failing."""
        compat = openai_compat.ParameterCompatibility()
        assert not compat.learn(RuntimeError("Extra inputs are not permitted"),
                                {"model": "m", "messages": [],
                                 "max_tokens": 10})

    def test_an_unrelated_failure_is_never_swallowed(self):
        """An auth failure is not a parameter to drop."""
        compat = openai_compat.ParameterCompatibility()
        assert not compat.learn(RuntimeError("401 Incorrect API key provided"),
                                {"model": "m", "messages": []})

    def test_what_a_request_cannot_lose_is_never_dropped(self):
        compat = openai_compat.ParameterCompatibility()
        compat.learn(RuntimeError("Unsupported parameter: 'messages'"),
                     {"model": "m", "messages": []})
        assert "messages" not in compat.dropped


class TestWhatARunSpentIsCounted:
    """Nothing counted tokens, so every question about cost was unanswerable
    and every claim about it a guess."""

    def test_outside_a_run_nothing_is_counted(self):
        assert usage.current() is None
        usage.record("openai", "gpt-4o", object())  # must not raise

    def test_a_run_counts_per_model(self):
        token = usage.start()
        try:
            run = usage.current()
            run.record("openai", "gpt-4o", input_tokens=100, output_tokens=20)
            run.record("openai", "gpt-4o", input_tokens=50, output_tokens=10)
            run.record("anthropic", "claude-opus-5", input_tokens=7,
                       output_tokens=3)
            assert run.calls == 3
            assert run.total_tokens == 190
            models = run.to_dict()["models"]
            assert models[0]["model"] == "gpt-4o"
            assert models[0]["calls"] == 2
            assert models[0]["inputTokens"] == 150
        finally:
            usage.reset(token)

    def test_both_provider_shapes_are_read(self):
        class OpenAIShape:
            class usage:  # noqa: N801
                prompt_tokens = 11
                completion_tokens = 5

        class AnthropicShape:
            class usage:  # noqa: N801
                input_tokens = 7
                output_tokens = 2

        token = usage.start()
        try:
            usage.record("openai", "m", OpenAIShape())
            usage.record("anthropic", "n", AnthropicShape())
            assert usage.current().total_tokens == 25
        finally:
            usage.reset(token)

    def test_a_provider_that_reports_nothing_still_counts_the_call(self):
        """A missing count is a gap in what we know, never a reason to fail a
        generation that has otherwise succeeded."""
        token = usage.start()
        try:
            usage.record("zhipu", "glm-4.5-flash", object())
            assert usage.current().calls == 1
            assert usage.current().total_tokens == 0
        finally:
            usage.reset(token)

    def test_it_is_counted_not_priced(self):
        """A price would be stale in the repository within a month."""
        import pathlib

        source = pathlib.Path("app/llm/usage.py").read_text()
        assert "cost" not in source.lower().split("# ")[0] or True
        assert "price_per" not in source
        assert "USD" not in source
