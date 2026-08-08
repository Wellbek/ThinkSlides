"""
The terminal ``done`` event ``_run_agent`` publishes must not contradict the
agent loop.

``loop.run()`` returns both when a run finishes and when it pauses on
``ask_user_question``. ``_run_agent`` then publishes its own ``done`` on top,
and both consumers of that event treat an unpaused ``done`` as "this session is
over": the orchestrator's SSE hub used to stop forwarding Redis events, and the
browser's EventSource closes. A pause that reports itself as finished therefore
tore the event pipe down while the user was still being asked a question, and
everything the resumed run emitted went nowhere until the page was reloaded.
"""

import json
import uuid

import pytest

from app.api import generate as generate_api


class _FakeDB:
    """Swallows the persistence ``_run_agent`` does around the event loop."""

    def add(self, _obj):
        pass

    async def commit(self):
        pass

    async def refresh(self, _obj):
        pass

    async def execute(self, *_args, **_kwargs):
        return None


class _FakeSessionFactory:
    def __call__(self):
        return self

    async def __aenter__(self):
        return _FakeDB()

    async def __aexit__(self, *_exc):
        return False


class _FakeRedis:
    def __init__(self):
        self.published: list[dict] = []

    async def get(self, _key):
        return None

    async def set(self, _key, _value, ex=None):
        pass

    async def publish(self, _channel, payload):
        self.published.append(json.loads(payload))
        return 1


def _fake_loop(events: list[dict]):
    class _Loop:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run(self, session_id):
            for event in events:
                yield event

    return _Loop


async def _run(monkeypatch, events: list[dict]) -> list[dict]:
    import app.db.session as db_session

    fake_redis = _FakeRedis()
    monkeypatch.setattr(db_session, "async_session", _FakeSessionFactory())
    monkeypatch.setattr(generate_api, "redis_client", fake_redis)
    monkeypatch.setattr(generate_api, "AgentLoop", _fake_loop(events))

    await generate_api._run_agent(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())

    return [e for e in fake_redis.published if e.get("type") == "done"]


@pytest.mark.asyncio
async def test_a_paused_run_publishes_a_paused_terminal_done(monkeypatch):
    dones = await _run(
        monkeypatch,
        [
            {"type": "thought", "phase": "content_analysis", "content": "thinking"},
            {"type": "question", "question": "Which aspect?", "options": ["A", "B"]},
            {"type": "done", "phase": "user_input", "content": "Waiting for user input", "paused": True},
        ],
    )

    assert dones, "no done event was published"
    assert all(d.get("paused") is True for d in dones), (
        "a terminal done without paused=True tells the SSE hub and the browser "
        f"to tear down mid-question: {dones}"
    )


@pytest.mark.asyncio
async def test_a_finished_run_publishes_an_unpaused_terminal_done(monkeypatch):
    dones = await _run(
        monkeypatch,
        [
            {"type": "thought", "phase": "rendering", "content": "rendering"},
            {"type": "done", "phase": "complete", "content": "All set."},
        ],
    )

    assert dones, "no done event was published"
    assert dones[-1].get("paused") is False
