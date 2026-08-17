"""Structured logging and the setup that installs it."""

from app.observability.logging import get_logger, setup_logging

__all__ = ["get_logger", "setup_logging"]
