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
