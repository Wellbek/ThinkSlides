"""
Tests for rate-limit handling in the LLM provider layer.

A token-per-minute ceiling is a routine, self-correcting condition - the
provider itself says "try again in 2.6s". Unretried, it surfaced as a failed
tool call, which mid-run abandoned the slide the agent was in the middle of
fixing and left the deck shipped with an unresolved defect.
"""

import asyncio

import pytest

from app.llm.provider import (
    MAX_RATE_LIMIT_RETRIES,
    _is_rate_limit_error,
    _rate_limit_delay,
    _with_rate_limit_retry,
)

_REAL_SLEEP = asyncio.sleep


@pytest.fixture
def no_sleep(monkeypatch):
    """Skip the real backoff wait. Bound to the original sleep, or the
    replacement would call itself."""

    async def _instant(_seconds):
        await _REAL_SLEEP(0)

    monkeypatch.setattr(asyncio, "sleep", _instant)


class _RateLimited(Exception):
    def __init__(self, message="Rate limit reached for gpt-4o", status_code=429, response=None):
        super().__init__(message)
        self.status_code = status_code
        self.response = response


class _Response:
    def __init__(self, headers=None, status_code=429):
        self.headers = headers or {}
        self.status_code = status_code


class TestRateLimitDetection:
    def test_a_429_status_is_a_rate_limit(self):
        assert _is_rate_limit_error(_RateLimited()) is True

    def test_a_nested_response_status_is_a_rate_limit(self):
        exc = Exception("something")
        exc.response = _Response()
        assert _is_rate_limit_error(exc) is True

    def test_the_reported_openai_message_is_recognised(self):
        message = (
            "Error code: 429 - {'error': {'message': 'Rate limit reached for gpt-4o in "
            "organization org-x on tokens per min (TPM): Limit 30000, Used 30000, "
            "Requested 1300. Please try again in 2.6s.', 'code': 'rate_limit_exceeded'}}"
        )
        assert _is_rate_limit_error(Exception(message)) is True

    def test_an_unrelated_error_is_not_a_rate_limit(self):
        assert _is_rate_limit_error(ValueError("bad json")) is False

    def test_a_500_is_not_a_rate_limit(self):
        assert _is_rate_limit_error(_RateLimited("server error", status_code=500)) is False


class TestRateLimitDelay:
    def test_the_providers_own_suggestion_is_used(self):
        exc = Exception("Please try again in 2.6s. Visit ...")
        # A cushion is added so the window has actually rolled over.
        assert 2.6 < _rate_limit_delay(exc, 0) <= 3.5

    def test_millisecond_suggestions_are_converted(self):
        exc = Exception("Please try again in 800ms.")
        assert 0.8 < _rate_limit_delay(exc, 0) < 2.0

    def test_a_retry_after_header_wins(self):
        exc = Exception("rate limit")
        exc.response = _Response(headers={"retry-after": "5"})
        assert _rate_limit_delay(exc, 0) == 5.0

    def test_a_retry_after_ms_header_is_scaled(self):
        exc = Exception("rate limit")
        exc.response = _Response(headers={"retry-after-ms": "1500"})
        assert _rate_limit_delay(exc, 0) == 1.5

    def test_it_falls_back_to_exponential_backoff(self):
        exc = Exception("rate limit, no hint")
        assert _rate_limit_delay(exc, 0) == 1.0
        assert _rate_limit_delay(exc, 2) == 4.0

    def test_the_wait_is_capped(self):
        exc = Exception("Please try again in 900s.")
        assert _rate_limit_delay(exc, 0) <= 30.0


class TestRetryBehaviour:
    @pytest.mark.asyncio
    async def test_a_transient_rate_limit_is_retried_and_succeeds(self, no_sleep):
        calls = {"n": 0}

        async def call():
            calls["n"] += 1
            if calls["n"] < 3:
                raise _RateLimited("Rate limit reached. Please try again in 1s.")
            return "ok"

        assert await _with_rate_limit_retry(call, provider="openai", model="gpt-4o") == "ok"
        assert calls["n"] == 3

    @pytest.mark.asyncio
    async def test_a_sync_callable_is_supported(self, no_sleep):
        """The Zhipu SDK is synchronous while the others are not."""
        calls = {"n": 0}

        def call():
            calls["n"] += 1
            if calls["n"] < 2:
                raise _RateLimited()
            return "sync-ok"

        result = await _with_rate_limit_retry(call, provider="zhipu", model="glm-4.7")
        assert result == "sync-ok"

    @pytest.mark.asyncio
    async def test_a_non_rate_limit_error_is_not_retried(self):
        calls = {"n": 0}

        async def call():
            calls["n"] += 1
            raise ValueError("bad request")

        with pytest.raises(ValueError):
            await _with_rate_limit_retry(call, provider="openai", model="gpt-4o")
        assert calls["n"] == 1, "a real error must surface immediately"

    @pytest.mark.asyncio
    async def test_retries_are_bounded(self, no_sleep):
        calls = {"n": 0}

        async def call():
            calls["n"] += 1
            raise _RateLimited()

        with pytest.raises(_RateLimited):
            await _with_rate_limit_retry(call, provider="openai", model="gpt-4o")
        assert calls["n"] == MAX_RATE_LIMIT_RETRIES + 1
