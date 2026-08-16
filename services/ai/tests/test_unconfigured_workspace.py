"""
A workspace with no model configured cannot generate, and says so.

There used to be no such state. ``resolve_chat_credentials`` fell back to the
process environment, so every organisation looked configured: ``chatConfigured``
was always true, the setup banner never appeared, and generations ran on
whatever key ``.env`` held. When that key turned out to be exhausted, the run
failed with a billing error naming a provider the user had never chosen and
could not find anywhere in their settings.

Settings is now the only source of a model, and its absence is a named,
reported condition.
"""

import uuid

import pytest

from app.agent import run_context
from app.agent.loop import AgentLoop
from app.llm.provider import AINotConfiguredError

pytestmark = pytest.mark.unconfigured_workspace


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


class TestTheRunRefusesToStart:
    @pytest.mark.asyncio
    async def test_configure_will_not_invent_a_provider(self):
        with pytest.raises(AINotConfiguredError):
            await AgentLoop(db=None, mode="simple")._configure()

    @pytest.mark.asyncio
    async def test_the_failure_reaches_the_user_as_one_actionable_error(self, monkeypatch):
        loop = AgentLoop(db=None, mode="simple")
        loop.redis_client = _FakeRedis()
        monkeypatch.setattr(loop, "_load_context", lambda *a, **k: _async({}))
        monkeypatch.setattr(loop, "_build_chat_messages", lambda *a, **k: _async([]))

        events = [e async for e in loop.run(uuid.uuid4())]

        errors = [e for e in events if e["type"] == "error"]
        assert len(errors) == 1
        assert errors[0]["code"] == "ai_not_configured"
        assert "Settings" in errors[0]["message"]

    @pytest.mark.asyncio
    async def test_a_saved_but_unusable_model_says_so_too(self, monkeypatch):
        """A provider id or model that no longer builds is not "unconfigured"
        in the same way, but it is just as unrunnable, and the user's next
        move is the same one."""
        from app.llm.provider import ProviderCredentials

        monkeypatch.setattr(
            "app.ai_settings.resolve_chat_credentials",
            lambda row: ProviderCredentials(provider="openai", model="", api_key="k"),
        )

        with pytest.raises(AINotConfiguredError, match="Settings"):
            await AgentLoop(db=None, mode="simple")._configure()


class TestNoToolQuietlyFallsBack:
    def test_a_tool_outside_a_configured_run_refuses(self):
        assert run_context.get_chat_provider() is None

        with pytest.raises(AINotConfiguredError):
            run_context.require_chat_provider()

    @pytest.mark.asyncio
    async def test_and_reports_it_as_the_tool_failing_rather_than_hanging(self):
        from app.agent.tools.extract_key_data import ExtractKeyDataTool

        result = await ExtractKeyDataTool.run(content="anything")

        assert not result.success
        assert "configured" in result.error
