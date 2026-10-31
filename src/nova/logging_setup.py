"""Structured logging with secret redaction.

Logs are plain key=value lines by default (readable in a terminal) and JSON when
``NOVA_LOG_JSON=true``. A redaction processor runs before any renderer so tokens
and other secrets never reach a log sink.
"""

from __future__ import annotations

import logging
import logging.handlers
import re
from typing import Any

import structlog

_REDACTED = "***redacted***"

# Keys whose values are never logged, matched case-insensitively.
_SENSITIVE_KEYS = frozenset(
    {
        "auth_token",
        "token",
        "password",
        "secret",
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "set-cookie",
        "session",
    }
)

# Patterns scrubbed from free-text log values.
_SENSITIVE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b(token|api[_-]?key|password|secret)\b\s*[=:]\s*\S+"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]+"),
)


def _scrub_text(value: str) -> str:
    for pattern in _SENSITIVE_PATTERNS:
        value = pattern.sub(lambda m: f"{m.group(0).split('=')[0].split(':')[0]}={_REDACTED}", value)
    return value


def redact_processor(
    _logger: Any, _name: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    """Replace sensitive values and scrub secret-looking substrings."""
    for key in list(event_dict):
        if key.lower() in _SENSITIVE_KEYS:
            event_dict[key] = _REDACTED
            continue
        value = event_dict[key]
        if isinstance(value, str):
            event_dict[key] = _scrub_text(value)
    return event_dict


def configure_logging(
    level: str = "INFO",
    *,
    json_logs: bool = False,
    log_file: str | None = None,
) -> None:
    """Configure stdlib logging and structlog once, at process start."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        handlers.append(
            logging.handlers.RotatingFileHandler(
                log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
            )
        )

    logging.basicConfig(
        format="%(message)s",
        level=numeric_level,
        handlers=handlers,
        force=True,
    )

    # Uvicorn's access log is noisy and duplicates our request logging.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("faster_whisper").setLevel(logging.WARNING)

    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if json_logs
        else structlog.dev.ConsoleRenderer(colors=False)
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=False),
            redact_processor,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
