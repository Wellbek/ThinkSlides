"""
Pytest configuration and shared fixtures.
"""

import pytest
import os


@pytest.fixture(autouse=True)
def set_test_env():
    """Set test environment variables."""
    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/thinkslides_test")
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")
    os.environ.setdefault("LLM_PROVIDER", "zhipu")
    os.environ.setdefault("ZHIPU_API_KEY", "test-key")
    os.environ.setdefault("JWT_SECRET", "test-jwt-secret")
    yield