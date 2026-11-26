"""JSON logging and Prometheus metrics."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram, generate_latest

_REDACT = {
    "password",
    "token",
    "secret",
    "api_key",
    "apikey",
    "authorization",
    "refresh_token",
    "client_secret",
}
_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime", "taskName"}


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
            "logger": record.name,
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            payload[key] = "[REDACTED]" if key.lower() in _REDACT else value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(level: str = "info", stream: object = None) -> None:
    """Logs go to stdout as JSON, or stderr in stdio MCP mode where stdout carries the protocol."""
    handler = logging.StreamHandler(stream or sys.stdout)  # type: ignore[arg-type]
    handler.setFormatter(JSONFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


registry = CollectorRegistry()

caldav_request_duration_seconds = Histogram(
    "caldav_request_duration_seconds",
    "Time spent on CalDAV calls, by operation and calendar source",
    ["method", "path"],
    registry=registry,
)
caldav_calendar_sync_errors_total = Counter(
    "caldav_calendar_sync_errors_total",
    "CalDAV calls that failed because the source was unavailable",
    ["calendar_id"],
    registry=registry,
)
api_request_duration_seconds = Histogram(
    "api_request_duration_seconds",
    "Time spent handling HTTP requests, by method and route template",
    ["method", "path"],
    registry=registry,
)
api_errors_total = Counter("api_errors_total", "HTTP responses with status >= 400", ["status_code"], registry=registry)


def render() -> tuple[bytes, str]:
    return generate_latest(registry), CONTENT_TYPE_LATEST
