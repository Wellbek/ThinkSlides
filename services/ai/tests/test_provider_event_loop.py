"""
The synchronous provider SDKs must not be called on the event loop.

``ZhipuProvider.chat`` used to call the (synchronous) zhipuai SDK directly, so
the uvicorn worker's loop was parked for the whole request - tens of seconds
for a tool-calling turn. Nothing in the service could make progress meanwhile:
not other sessions' generations, not SSE delivery, not health checks.

These tests pin the property that matters rather than the mechanism: while a
provider call is in flight, the loop still runs other tasks.
"""

import asyncio
import time
from types import SimpleNamespace

from app.llm.provider import ZhipuProvider


CALL_DURATION_S = 0.30
HEARTBEAT_INTERVAL_S = 0.01


def _fake_response(content: str = "ok"):
    """The shape ZhipuProvider.chat reads off an SDK response."""
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=None),
                finish_reason="stop",
            )
        ]
    )


class _BlockingClient:
    """Stands in for the synchronous zhipuai client."""

    def __init__(self, duration_s: float = CALL_DURATION_S):
        self.duration_s = duration_s
        self.calls = 0
        self.thread_names: list[str] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        import threading

        self.calls += 1
        self.thread_names.append(threading.current_thread().name)
        time.sleep(self.duration_s)  # a real blocking network round-trip
        return _fake_response()


def _provider_with(client) -> ZhipuProvider:
    provider = ZhipuProvider.__new__(ZhipuProvider)  # skip SDK construction
    provider.client = client
    provider.model = "glm-4.5-flash"
    return provider


async def _heartbeat(stop: asyncio.Event) -> int:
    """Count how many times the loop got to run us while the call was busy."""
    ticks = 0
    while not stop.is_set():
        await asyncio.sleep(HEARTBEAT_INTERVAL_S)
        ticks += 1
    return ticks


async def test_chat_does_not_block_the_event_loop():
    client = _BlockingClient()
    provider = _provider_with(client)

    stop = asyncio.Event()
    beat = asyncio.create_task(_heartbeat(stop))

    result = await provider.chat(messages=[{"role": "user", "content": "hi"}])
    stop.set()
    ticks = await beat

    assert result["message"]["content"] == "ok"
    assert client.calls == 1

    # A blocking call on the loop yields zero ticks. Allow generous slack for a
    # loaded CI box: even a quarter of the theoretical rate proves the loop ran.
    expected = CALL_DURATION_S / HEARTBEAT_INTERVAL_S
    assert ticks >= expected * 0.25, (
        f"event loop was starved during the provider call: {ticks} heartbeats, "
        f"expected roughly {expected:.0f}"
    )


async def test_chat_runs_off_the_main_thread():
    """The blocking work belongs on an executor thread, not the loop's."""
    import threading

    client = _BlockingClient(duration_s=0.01)
    provider = _provider_with(client)

    await provider.chat(messages=[{"role": "user", "content": "hi"}])

    assert client.thread_names, "SDK was never called"
    assert client.thread_names[0] != threading.current_thread().name


async def test_concurrent_chats_overlap():
    """
    Two generations at once should take about as long as one, not two in
    series - the case the issue calls out for ``--workers 2``.
    """
    provider_a = _provider_with(_BlockingClient())
    provider_b = _provider_with(_BlockingClient())

    started = time.perf_counter()
    await asyncio.gather(
        provider_a.chat(messages=[{"role": "user", "content": "a"}]),
        provider_b.chat(messages=[{"role": "user", "content": "b"}]),
    )
    elapsed = time.perf_counter() - started

    # Serialised on the loop this is 2 x CALL_DURATION_S.
    assert elapsed < CALL_DURATION_S * 1.6, (
        f"concurrent provider calls serialised: took {elapsed:.2f}s for two "
        f"{CALL_DURATION_S:.2f}s calls"
    )
