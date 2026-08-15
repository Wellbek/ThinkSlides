"""
Pytest configuration and shared fixtures.

Environment variables are set at module import time (not in a fixture) because
``app.config.settings`` is instantiated once when the app package is first
imported - which happens during test collection, before any fixture runs. To
make the storage dirs writable on any host (not just the Docker ``/app``
layout) and keep tests hermetic, they point at per-session temp directories.
"""

import os
import tempfile


def _temp_dir(prefix: str) -> str:
    return tempfile.mkdtemp(prefix=prefix)


# Storage dirs - hermetic temp dirs so renderer/preview tests don't depend on
# a writable /app (the Docker-only layout).
os.environ.setdefault("TEMPLATE_DIR", _temp_dir("ts_templates_"))
os.environ.setdefault("OUTPUT_DIR", _temp_dir("ts_output_"))
os.environ.setdefault("PREVIEW_OUTPUT_DIR", _temp_dir("ts_previews_"))

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/thinkslides_test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")
os.environ.setdefault("LLM_PROVIDER", "zhipu")
os.environ.setdefault("ZHIPU_API_KEY", "test-key")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def configured_workspace(request, monkeypatch):
    """Run every test inside a workspace that has a model configured.

    A model now comes from the workspace's saved settings and from nowhere
    else - there is no environment fallback - so a loop constructed with
    ``db=None, org_id=None`` resolves to nothing and refuses to run. That is
    the point of the change, but it is not what most tests are about: they
    stub ``_chat_with_retry`` or the tool's own provider and never make a real
    call. This stands in for the settings row they would otherwise all have to
    seed.

    Tests that exercise the unconfigured state ask for it by name:

        @pytest.mark.unconfigured_workspace
    """
    if request.node.get_closest_marker("unconfigured_workspace"):
        return

    from app.llm.provider import ProviderCredentials

    monkeypatch.setattr(
        "app.ai_settings.resolve_chat_credentials",
        lambda row: ProviderCredentials(
            provider="openai", model="gpt-4o", api_key="test-key"
        ),
    )
    monkeypatch.setattr(
        "app.agent.loop.build_chat_provider", lambda credentials: _StubProvider()
    )


class _StubProvider:
    """Stands in for a real provider. Calling it is a test bug, not a fallback."""

    async def chat(self, messages, tools=None):
        raise AssertionError(
            "the stub chat provider was called - the test meant to patch "
            "_chat_with_retry or the tool's own provider"
        )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "unconfigured_workspace: run without a configured chat model",
    )
