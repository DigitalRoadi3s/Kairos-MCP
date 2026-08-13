"""
Structured JSON logging (task 18, Haiku tier). NFR-4.

Import and call setup_logging() once at app startup, before any other
module logs. Formats every record as JSON with timestamp, level, and
whatever `extra={...}` fields the caller passed. Never logs passwords
or tokens - existing call sites already avoid passing them in `extra`
(caldav_client.py's docstrings note "never log this" next to
self._password), so this formatter doesn't need its own redaction
logic on top of that discipline, but it does defensively strip any
key literally named password/token/secret if one ever slips through.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

_REDACT_KEYS = {"password", "token", "secret", "api_key", "apikey"}

_RESERVED_LOG_RECORD_ATTRS = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "taskName",
}


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
            "logger": record.name,
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED_LOG_RECORD_ATTRS or key.startswith("_"):
                continue
            if key.lower() in _REDACT_KEYS:
                payload[key] = "[REDACTED]"
            else:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(level: str = "info") -> None:
    """
    Called once from main.py at import time (before the FastAPI app is
    constructed, so startup-time logs are also formatted). Per PRD:
    logs go to stdout only, no file logging.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
