"""
The provider SDKs must not retry underneath our retry layer.

``AsyncOpenAI`` and friends retry 429s internally by default. That happens
below ``_with_rate_limit_retry``, so the exception never reaches it: no
``rate_limit`` event is published, nothing counts down, and the interface shows
a step sitting there for half a minute looking exactly like a hang. A live run
logged

    {"event": "Retrying request to /chat/completions in 24.886000 seconds"}

- the SDK's own line, not ours - while the UI showed nothing for it at all.

The narration only ever covered 429s the SDK gave up on. One retry policy, in
one place, is the fix; these tests pin that the clients are constructed to
honour it.
"""

import inspect

import pytest

from app.llm import provider as provider_module
from app.llm.provider import (
    SDK_MAX_RETRIES,
    AnthropicProvider,
    OpenAIProvider,
    _with_rate_limit_retry,
)
from app.llm.retry_notices import (
    REASON_RATE_LIMIT,
    RetryNotice,
    reset_retry_observer,
    set_retry_observer,
)


def test_sdk_retries_are_disabled():
    """Zero, not "some" - our layer owns the policy end to end."""
    assert SDK_MAX_RETRIES == 0


def test_every_sdk_client_is_constructed_with_the_retry_cap():
    """
    Each client construction has to pass max_retries, or that provider quietly
    keeps its own policy. Reading the source catches a new client added without
    it, which is the way this regresses.
    """
    source = inspect.getsource(provider_module)

    constructions = [
        ("AsyncOpenAI(", source.count("AsyncOpenAI(**kwargs)")),
        ("AsyncAzureOpenAI(", source.count("AsyncAzureOpenAI(")),
        ("AsyncAnthropic(", source.count("AsyncAnthropic(**kwargs)")),
    ]
    for name, count in constructions:
        assert count > 0, f"expected to find a {name} construction to check"

    # Every construction site must mention the cap.
    assert source.count("max_retries") >= sum(c for _, c in constructions), (
        "a provider client is constructed without max_retries; it will retry "
        "429s below _with_rate_limit_retry and the wait will be invisible"
    )


@pytest.mark.parametrize(
    "factory",
    [
        lambda: OpenAIProvider(model="gpt-4o", api_key="test-key"),
        lambda: AnthropicProvider(model="claude-sonnet-5", api_key="test-key"),
    ],
    ids=["openai", "anthropic"],
)
def test_constructed_clients_report_no_retries(factory):
    """The SDK object itself agrees, not just the kwargs we passed."""
    try:
        provider = factory()
    except Exception as exc:  # pragma: no cover - SDK not installed
        pytest.skip(f"SDK unavailable: {exc}")

    assert provider.client.max_retries == 0


class _RateLimited(Exception):
    """Stands in for the SDK's 429, which our detector matches on text."""

    def __init__(self):
        super().__init__("Error code: 429 - rate limit exceeded")
        self.status_code = 429


async def test_a_429_reaches_our_layer_and_is_narrated(monkeypatch):
    """
    The behaviour the user actually missed: a 429 produces a rate_limit notice
    with a countdown, instead of being absorbed in silence.
    """
    real_sleep = __import__("asyncio").sleep

    async def _instant(_seconds):
        await real_sleep(0)

    monkeypatch.setattr(__import__("asyncio"), "sleep", _instant)

    notices: list[RetryNotice] = []
    token = set_retry_observer(notices.append)
    try:
        attempts = {"n": 0}

        async def call():
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise _RateLimited()
            return "ok"

        result = await _with_rate_limit_retry(call, provider="zhipu", model="glm-4.7")
    finally:
        reset_retry_observer(token)

    assert result == "ok"
    assert attempts["n"] == 2
    assert len(notices) == 1, "the wait was not narrated"

    notice = notices[0]
    assert notice.reason == REASON_RATE_LIMIT
    assert notice.delay_seconds > 0, "a countdown of zero tells the user nothing"
    assert notice.provider == "zhipu"
    assert notice.model == "glm-4.7"
