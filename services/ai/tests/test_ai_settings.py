"""Tests for per-organisation AI settings: redaction, resolution, gating.

Resolution is what this module is about, so it opts out of the
configured-workspace fixture in conftest and reads the real functions.
"""

import base64
import os
import uuid

import pytest

from app import ai_settings
from app.api.settings import ModelRoleInput, _to_section, describe_provider_error
from app.config import settings as env_settings
from app.db.models import OrganisationAISettings
from app.security.crypto import ACTIVE_KEY_ENV, get_cipher, reset_cipher_cache

pytestmark = pytest.mark.unconfigured_workspace

ORG_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    monkeypatch.setenv(ACTIVE_KEY_ENV, base64.b64encode(os.urandom(32)).decode())
    reset_cipher_cache()
    yield
    reset_cipher_cache()


@pytest.fixture
def no_env_provider(monkeypatch):
    """Simulate a deployment with no provider configured in the environment."""
    monkeypatch.setattr(env_settings, "zhipu_api_key", "")
    monkeypatch.setattr(env_settings, "web_search_provider", "zhipu")
    monkeypatch.setattr(env_settings, "tavily_api_key", "")


def _row(**kwargs) -> OrganisationAISettings:
    """Build a settings row, encrypting any plaintext `*_key` kwargs."""
    row = OrganisationAISettings(organisation_id=ORG_ID)
    for field in (ai_settings.CHAT_KEY_FIELD, ai_settings.SEARCH_KEY_FIELD,
                  ai_settings.BACKEND_KEY_FIELD):
        plaintext = kwargs.pop(field, None)
        if plaintext:
            envelope = get_cipher().encrypt(
                plaintext, ai_settings.encryption_context(ORG_ID, field)
            )
            setattr(row, f"{field}_encrypted", envelope)
            setattr(row, f"{field}_hint", f"****{plaintext[-4:]}")
    for key, value in kwargs.items():
        setattr(row, key, value)
    return row


# ── Redaction ──────────────────────────────────────────────────────────


def test_redacted_payload_never_carries_key_material():
    """The only thing that escapes is a last-four hint and a boolean."""
    row = _row(
        chat_provider="anthropic",
        chat_model="claude-opus-5",
        chat_api_key="sk-ant-supersecret-9876",
    )

    payload = ai_settings.redacted_payload(row)

    assert payload["chat"]["apiKeyHint"] == "****9876"
    assert payload["chat"]["hasApiKey"] is True
    assert "supersecret" not in str(payload)


def test_redacted_payload_for_an_unconfigured_org_is_all_empty():
    """A fresh organisation renders as blank fields, not an error."""
    payload = ai_settings.redacted_payload(None)

    assert payload["chat"] == {
        "provider": None, "model": None, "baseUrl": None,
        "apiKeyHint": None, "hasApiKey": False,
    }
    assert payload["searchBackend"]["hasApiKey"] is False


# ── Chat resolution ────────────────────────────────────────────────────


def test_stored_chat_config_resolves_to_provider_credentials():
    """The stored key is decrypted only at the point of use."""
    row = _row(
        chat_provider="anthropic",
        chat_model="claude-opus-5",
        chat_base_url="https://proxy.internal",
        chat_api_key="sk-ant-abcd",
    )

    credentials = ai_settings.resolve_chat_credentials(row)

    assert credentials.provider == "anthropic"
    assert credentials.model == "claude-opus-5"
    assert credentials.api_key == "sk-ant-abcd"
    assert credentials.base_url == "https://proxy.internal"


def test_an_unconfigured_org_resolves_to_nothing_whatever_the_environment_holds(monkeypatch):
    """Settings is the only source of a model.

    There was a fallback to the process environment here, and it meant a
    workspace that had never been configured still reported chatConfigured -
    so the setup banner never appeared, and generations ran on a key nobody
    had chosen in the UI until it failed for reasons nobody could act on.
    """
    monkeypatch.setattr(env_settings, "zhipu_api_key", "id.secret")
    monkeypatch.setattr(env_settings, "zhipu_model", "glm-4.7")

    assert ai_settings.resolve_chat_credentials(None) is None


def test_chat_is_unconfigured_when_nothing_is_saved(no_env_provider):
    """This is what raises the setup banner in the UI."""
    assert ai_settings.resolve_chat_credentials(None) is None
    assert ai_settings.configuration_status(None)["chatConfigured"] is False


def test_a_partially_configured_role_is_treated_as_unconfigured(no_env_provider):
    """A provider with no model or key must not produce half-built credentials."""
    row = _row(chat_provider="anthropic")

    assert ai_settings.resolve_chat_credentials(row) is None


def test_an_unreadable_key_degrades_to_unconfigured(monkeypatch, no_env_provider):
    """A rotated-away KEK must not 500 every request."""
    row = _row(chat_provider="anthropic", chat_model="claude-opus-5", chat_api_key="sk-x1234")
    monkeypatch.setenv(ACTIVE_KEY_ENV, base64.b64encode(os.urandom(32)).decode())
    reset_cipher_cache()

    assert ai_settings.resolve_chat_credentials(row) is None


# ── Deep search resolution and gating ──────────────────────────────────


def test_deep_search_model_falls_back_to_the_chat_model():
    """So an org configuring only a backend does not enter the same key twice."""
    row = _row(chat_provider="anthropic", chat_model="claude-opus-5", chat_api_key="sk-ant-abcd")

    credentials = ai_settings.resolve_deep_search_model(row)

    assert (credentials.provider, credentials.model) == ("anthropic", "claude-opus-5")


def test_native_backend_is_available_for_anthropic():
    """Claude's server-side search needs no extra subscription."""
    row = _row(
        search_backend="native",
        chat_provider="anthropic", chat_model="claude-opus-5", chat_api_key="sk-ant-abcd",
    )

    config, reason = ai_settings.resolve_search_config(row)

    assert (config.backend, reason) == ("native", "")


def test_native_backend_is_refused_for_a_non_search_openai_model():
    """Only the '-search-preview' models can search via chat completions."""
    row = _row(
        search_backend="native",
        search_provider="openai", search_model="gpt-4o", search_api_key="sk-abcd",
    )

    config, reason = ai_settings.resolve_search_config(row)

    assert config is None
    assert "search-preview" in reason


def test_native_backend_is_refused_for_a_zhipu_key_that_cannot_sign():
    """Zhipu's search endpoint needs an 'id.secret' key to build its bearer JWT."""
    row = _row(
        search_backend="native",
        search_provider="zhipu", search_model="glm-4.7", search_api_key="plainkey",
    )

    config, reason = ai_settings.resolve_search_config(row)

    assert config is None
    assert "id.secret" in reason


def test_external_backend_without_a_key_disables_deep_thinking():
    """The reason is phrased for the user, since the UI shows it verbatim."""
    row = _row(search_backend="firecrawl")

    config, reason = ai_settings.resolve_search_config(row)

    assert config is None
    assert "Firecrawl" in reason


def test_external_backend_with_a_key_resolves():
    row = _row(search_backend="tavily", search_backend_api_key="tvly-abcd")

    config, reason = ai_settings.resolve_search_config(row)

    assert (config.backend, config.api_key, reason) == ("tavily", "tvly-abcd", "")


def test_unknown_backend_is_reported_rather_than_crashing():
    """Guards against a row written by a newer version of the service."""
    config, reason = ai_settings.resolve_search_config(_row(search_backend="bing"))

    assert config is None
    assert "bing" in reason


def test_a_search_backend_in_the_environment_is_ignored_too(monkeypatch):
    """Same rule as the chat model: saved settings or nothing.

    An env-configured backend made Deep Thinking look available on a workspace
    that had configured nothing, which is the same lie in a smaller place.
    """
    monkeypatch.setattr(env_settings, "web_search_provider", "tavily")
    monkeypatch.setattr(env_settings, "tavily_api_key", "tvly-env")

    config, reason = ai_settings.resolve_search_config(None)

    assert config is None
    assert reason


def test_status_reports_why_deep_thinking_is_unavailable(no_env_provider):
    """The frontend disables the Deep Thinking option and shows this reason."""
    status = ai_settings.configuration_status(_row(search_backend="firecrawl"))

    assert status["deepThinkingAvailable"] is False
    assert "Firecrawl" in status["deepThinkingUnavailableReason"]


def test_a_workspace_with_nothing_configured_is_told_what_to_do_first(no_env_provider):
    """Deep Thinking searches with the reasoning model unless another source
    is picked, so "no Deep Thinking model" was naming a second model the user
    had no reason to think about - on the screen that also says no model at
    all is set up."""
    reason = ai_settings.configuration_status(None)["deepThinkingUnavailableReason"]

    assert "reasoning model" in reason
    assert "Deep Thinking model" not in reason


def test_status_reports_encryption_availability(monkeypatch):
    """Surfaces a missing SETTINGS_ENCRYPTION_KEY before the user types a key."""
    assert ai_settings.configuration_status(None)["encryptionAvailable"] is True

    monkeypatch.delenv(ACTIVE_KEY_ENV, raising=False)
    reset_cipher_cache()

    assert ai_settings.configuration_status(None)["encryptionAvailable"] is False


# ── API request translation ────────────────────────────────────────────


def test_omitted_api_key_is_not_part_of_the_update():
    """An omitted field must leave the stored key alone, not clear it."""
    section = _to_section(ModelRoleInput(provider="anthropic", model="claude-opus-5"))

    assert section == {"provider": "anthropic", "model": "claude-opus-5"}
    assert "api_key" not in section


def test_explicit_empty_api_key_clears_the_stored_key():
    """Distinct from omission - this is how a user removes a credential."""
    section = _to_section(ModelRoleInput(apiKey=""))

    assert section == {"api_key": ""}


def test_new_api_key_is_carried_through():
    section = _to_section(ModelRoleInput(apiKey="sk-new"))

    assert section == {"api_key": "sk-new"}


@pytest.mark.parametrize(
    "status,expected",
    [
        (401, "Authentication failed"),
        (403, "not permitted"),
        (404, "model was not found"),
        (429, "Rate limited"),
        (500, "server error"),
    ],
)
def test_provider_errors_are_translated_into_actionable_messages(status, expected):
    """Users see what to fix, not a raw SDK repr."""
    error = Exception("boom")
    error.status_code = status

    assert expected in describe_provider_error(error)


def test_connection_errors_point_at_the_base_url():
    """The most common misconfiguration for self-hosted or proxied endpoints."""
    assert "base URL" in describe_provider_error(Exception("Connection timeout"))


def test_the_test_prompt_budget_survives_a_reasoning_preamble():
    """Regression: a 32-token budget made valid GLM credentials look broken.

    The model spent every token on its thinking preamble and returned
    finish_reason=length with empty content, so a working key reported an
    empty reply. See TEST_MAX_TOKENS in app/api/settings.py.
    """
    from app.api.settings import TEST_MAX_TOKENS

    assert TEST_MAX_TOKENS >= 128


def test_the_search_test_query_is_phrased_as_a_question():
    """Regression: keyword phrasing returned irrelevant sources.

    "height of Mount Everest in metres" made Tavily key off "height" and
    return pages about human height, which reads as a broken backend.
    Backends tuned for LLM grounding answer natural questions far better.
    """
    from app.api.settings import TEST_SEARCH_QUERY

    assert TEST_SEARCH_QUERY.endswith("?")


def test_settings_ignores_unrelated_env_file_keys(tmp_path, monkeypatch):
    """Regression: the shared repo-root .env made `app` unimportable.

    `.env` is the one file the setup docs tell you to create, and it holds the
    web and orchestrator settings alongside the AI service's. With pydantic's
    default `extra="forbid"`, each of those raised `extra_forbidden` from the
    module-level `Settings()` call, so importing anything under `app.` failed
    outside Docker.
    """
    from app.config import Settings

    # The real process environment wins over an env file, and conftest sets
    # ZHIPU_API_KEY - clear it so the file is what's actually under test.
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "ZHIPU_API_KEY=from-env-file\n"
        "PORT=8080\n"
        "NEXT_PUBLIC_ORCHESTRATOR_URL=http://localhost:8080\n"
        "AI_SERVICE_URL=http://ai-service:8000\n"
    )

    loaded = Settings(_env_file=str(env_file))

    assert loaded.zhipu_api_key == "from-env-file"
    assert not hasattr(loaded, "port")
