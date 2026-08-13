"""
Prometheus metrics (task 19, Haiku tier). NFR-4.

Exposes exactly the metrics named in the PRD:
- caldav_request_duration_seconds (histogram, by method/path)
- caldav_calendar_sync_errors_total (counter, by calendar_id)
- api_request_duration_seconds (histogram, by method/path)
- api_errors_total (counter, by status_code)
- caldav_rate_limit_remaining (gauge, by calendar_id) - iCloud gotcha #4

main.py's /metrics endpoint calls render() and returns it with the
correct content type. Call sites (main.py handlers) call the small
helper functions below rather than touching prometheus_client objects
directly, keeping this module the single place that knows the metric
names/labels.
"""
from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, Histogram, generate_latest

registry = CollectorRegistry()

caldav_request_duration_seconds = Histogram(
    "caldav_request_duration_seconds",
    "Time spent on CalDAV client calls",
    ["method", "path"],
    registry=registry,
)

caldav_calendar_sync_errors_total = Counter(
    "caldav_calendar_sync_errors_total",
    "CalDAV sync/request errors",
    ["calendar_id"],
    registry=registry,
)

api_request_duration_seconds = Histogram(
    "api_request_duration_seconds",
    "Time spent handling REST API requests",
    ["method", "path"],
    registry=registry,
)

api_errors_total = Counter(
    "api_errors_total",
    "REST API errors",
    ["status_code"],
    registry=registry,
)

caldav_rate_limit_remaining = Gauge(
    "caldav_rate_limit_remaining",
    "iCloud CalDAV X-RateLimit-Remaining, per source",
    ["calendar_id"],
    registry=registry,
)


def render() -> tuple[bytes, str]:
    """Returns (body, content_type) for the /metrics endpoint."""
    return generate_latest(registry), CONTENT_TYPE_LATEST
