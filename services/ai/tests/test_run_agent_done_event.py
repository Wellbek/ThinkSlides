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
        self.deleted: list[str] = []

    async def get(self, _key):
        return None

    async def set(self, _key, _value, ex=None):
        pass

    async def publish(self, _channel, payload):
        self.published.append(json.loads(payload))
        return 1

    async def delete(self, key):
        self.deleted.append(key)
        return 1


def _fake_loop(events: list[dict]):
    class _Loop:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run(self, session_id):
            for event in events:
                yield event

    return _Loop


async def _run(monkeypatch, events: list[dict]) -> tuple[list[dict], _FakeRedis]:
    import app.db.session as db_session

    fake_redis = _FakeRedis()
    monkeypatch.setattr(db_session, "async_session", _FakeSessionFactory())
    monkeypatch.setattr(generate_api, "redis_client", fake_redis)
    monkeypatch.setattr(generate_api, "AgentLoop", _fake_loop(events))

    session_id = uuid.uuid4()
    await generate_api._run_agent(session_id, uuid.uuid4(), uuid.uuid4())

    dones = [e for e in fake_redis.published if e.get("type") == "done"]
    return dones, fake_redis, session_id


@pytest.mark.asyncio
async def test_a_paused_run_publishes_a_paused_terminal_done(monkeypatch):
    dones, fake_redis, _session_id = await _run(
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
    assert not fake_redis.deleted, (
        "a genuinely paused run must not clear working_state - the next "
        "message needs it to resume"
    )


@pytest.mark.asyncio
async def test_a_finished_run_publishes_an_unpaused_terminal_done(monkeypatch):
    dones, fake_redis, session_id = await _run(
        monkeypatch,
        [
            {"type": "thought", "phase": "rendering", "content": "rendering"},
            {"type": "done", "phase": "complete", "content": "All set."},
        ],
    )

    assert dones, "no done event was published"
    assert dones[-1].get("paused") is False
    assert f"working_state:{session_id}" in fake_redis.deleted


@pytest.mark.asyncio
async def test_a_run_that_errors_out_does_not_report_success(monkeypatch):
    """
    The loop can end on a ``type: "error"`` event without raising - a
    provider failure or a stalled phase yields one and returns rather than
    propagating an exception. Nothing else sets the reply text for that case,
    so without explicit handling the trailing "ensure we have text to show"
    fallback used to claim "Presentation generation completed" for a run that
    had just failed outright.
    """
    dones, fake_redis, session_id = await _run(
        monkeypatch,
        [
            {"type": "thought", "phase": "content_generation", "content": "designing"},
            {
                "type": "error",
                "phase": "content_generation",
                "message": "Rate limited by openai after 6 attempts.",
                "code": "provider_unavailable",
            },
            {"type": "done", "phase": "content_generation", "paused": False},
        ],
    )

    assert dones, "no done event was published"
    final = dones[-1]
    assert final.get("paused") is False
    assert "completed" not in final.get("content", "").lower(), (
        f"a run that errored out must not report success: {final}"
    )
    assert "Rate limited by openai" in final.get("content", "")


@pytest.mark.asyncio
async def test_an_errored_run_clears_working_state_so_the_next_message_is_not_misread(monkeypatch):
    """
    working_state:{session_id} is what the orchestrator's /messages handler
    checks to decide a new message is actually the answer to an old question.
    It used to be cleared only on a genuine finish - never on error - so a
    run that failed left it behind, and the next, completely unrelated
    message a user sent got silently routed as an answer to a question that
    was several turns stale by then.
    """
    _dones, fake_redis, session_id = await _run(
        monkeypatch,
        [
            {"type": "error", "phase": "content_generation", "message": "boom"},
            {"type": "done", "phase": "content_generation", "paused": False},
        ],
    )

    assert f"working_state:{session_id}" in fake_redis.deleted
