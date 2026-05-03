"""Structured logging configuration."""

import logging
import sys

import structlog


def configure_logging(log_level: str = "INFO") -> None:
    """Configure standard and structured logging."""

    timestamper = structlog.processors.TimeStamper(fmt="iso")
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        timestamper,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=shared_processors + [structlog.processors.JSONRenderer()],
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(level=getattr(logging, log_level.upper(), logging.INFO), stream=sys.stdout)


def get_logger(name: str):
    """Create a structured logger instance."""

    return structlog.get_logger(name)

