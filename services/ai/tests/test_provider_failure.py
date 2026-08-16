"""
What a run does when the model provider will not answer.

Reproduced live: the workspace's Zhipu account had run out of credit, which
that provider reports as HTTP 429 with code 1113 (余额不足, "insufficient
balance"). The provider layer already knows waiting cannot fix that and
declines to retry it. The agent loop's own wrapper saw only the 429, retried
three times at 5s, 10s and 20s, and then reported two errors: the raw vendor
JSON, and - because the loop had broken out before finalizing - a claim that
generation had not completed within 25 steps, advising the user to simplify a
request that was never the problem.
"""

import uuid

import pytest

from app.agent.loop import AgentLoop, _is_retryable, _provider_failure_message


class _Quota(Exception):
    status_code = 429

    def __str__(self):
        return (
            'Error code: 429, with error text {"error":{"code":"1113",'
            '"message":"余额不足或无可用资源包,请充值。"}}'
        )


class _Busy(Exception):
    status_code = 429

    def __str__(self):
        return "Error code: 429 - rate limit reached, try again in 2.6s"


class _Down(Exception):
    status_code = 503


class TestEveryLayerAgreesOnWhatIsWorthWaitingFor:
    def test_an_exhausted_account_is_not_retried(self):
        assert _is_retryable(_Quota()) is False

    def test_a_busy_provider_still_is(self):
        assert _is_retryable(_Busy()) is True

    def test_so_is_a_provider_that_fell_over(self):
        assert _is_retryable(_Down()) is True

    @pytest.mark.asyncio
    async def test_the_loop_gives_up_on_it_immediately(self):
        loop = AgentLoop(db=None, mode="simple")
        attempts = {"n": 0}

        class _Provider:
            async def chat(self, messages, tools=None):
                attempts["n"] += 1
                raise _Quota()

        loop.llm = _Provider()

        with pytest.raises(_Quota):
            await loop._chat_with_retry(messages=[], tools=None)

        assert attempts["n"] == 1


class TestTheUserIsToldWhatToDo:
    def test_an_exhausted_account_is_named_as_one(self):
        message = _provider_failure_message(_Quota())

        assert "out of credit" in message
        assert "1113" not in message

    def test_a_rate_limit_is_named_as_one(self):
        assert "rate-limiting" in _provider_failure_message(_Busy())

    def test_anything_else_carries_the_detail(self):
        assert "boom" in _provider_failure_message(RuntimeError("boom"))


class TestOneFailureIsReportedOnce:
    @pytest.mark.asyncio
    async def test_the_step_cap_is_not_blamed_for_a_provider_outage(self, monkeypatch):
        loop = AgentLoop(db=None, mode="simple")
        loop.questions_declined = True
        loop.redis_client = _FakeRedis()

        async def _fails(*args, **kwargs):
            raise _Quota()

        monkeypatch.setattr(loop, "_load_context", lambda *a, **k: _async({}))
        monkeypatch.setattr(loop, "_build_chat_messages", lambda *a, **k: _async([]))
        monkeypatch.setattr(loop, "_chat_with_retry", _fails)

        events = [e async for e in loop.run(uuid.uuid4())]

        errors = [e for e in events if e["type"] == "error"]
        assert len(errors) == 1
        assert errors[0]["code"] == "provider_unavailable"


class _FakeRedis:
    def __init__(self):
        self.store = {}

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, key):
        self.store.pop(key, None)


async def _async(value):
    return value
