"""
Config manager (task 2, Sonnet tier). FR-1 / NFR-5.

Loads calendar source definitions from the CALDAV_SOURCES env var (JSON
array) or a mounted config.yaml, builds a CalDAVSourceClient per source
(caldav_client.py, task 3), and validates connectivity for each at
startup. main.py calls load_and_validate() once during app startup.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Optional

from caldav_client import (
    CalDAVAuthError,
    CalDAVNotFoundError,
    CalDAVSourceClient,
    CalDAVUnavailableError,
)
from circuit_breaker import CircuitBreaker
import google_oauth
import metrics

logger = logging.getLogger("caldav_gateway.config")


@dataclass
class SourceConfig:
    id: str
    name: str
    url: str
    auth_type: str = "basic"  # "basic" | "oauth2" (task 24, Google Calendar)
    username: Optional[str] = None
    password: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    refresh_token: Optional[str] = None
    calendar_path: Optional[str] = None
    writable: bool = True
    timezone: str = "UTC"


class ConfigError(Exception):
    """Raised on malformed CALDAV_SOURCES or missing required fields."""


CALDAV_METHODS_TO_WRAP = ("list_events", "get_event", "create_event", "update_event", "delete_event")


def _wrap_with_breaker(client: CalDAVSourceClient) -> CircuitBreaker:
    """
    Wraps the CalDAV-hitting methods on `client` so every call goes
    through a per-source CircuitBreaker (task 16) and gets timed/counted
    for Prometheus (task 19) transparently - no call site in main.py/
    daily_brief.py needs to know either exists.
    """
    breaker = CircuitBreaker(client.source_id)
    for method_name in CALDAV_METHODS_TO_WRAP:
        original = getattr(client, method_name)

        def make_wrapped(fn, method_name=method_name):
            def wrapped(*args, **kwargs):
                with metrics.caldav_request_duration_seconds.labels(
                        method=method_name, path=client.source_id).time():
                    try:
                        return breaker.call(lambda: fn(*args, **kwargs))
                    except CalDAVUnavailableError:
                        metrics.caldav_calendar_sync_errors_total.labels(
                            calendar_id=client.source_id).inc()
                        raise
            return wrapped

        setattr(client, method_name, make_wrapped(original))
    return breaker


def _load_source_configs() -> list[SourceConfig]:
    """
    Reads CALDAV_SOURCES (JSON array, per PRD FR-1 / Appendix A Step 4).
    Falls back to /config/sources.yaml if the env var is absent and the
    file exists (mounted read-only per PRD "Volume Mounts").
    """
    raw = os.environ.get("CALDAV_SOURCES")
    if raw:
        try:
            entries = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"CALDAV_SOURCES is not valid JSON: {exc}") from exc
    else:
        config_path = "/config/sources.yaml"
        if not os.path.exists(config_path):
            raise ConfigError(
                "No CALDAV_SOURCES env var and no /config/sources.yaml found. "
                "At least one calendar source is required."
            )
        import yaml  # local import: only needed on this fallback path
        with open(config_path) as f:
            data = yaml.safe_load(f)
        entries = data.get("sources", [])

    if not entries:
        raise ConfigError("CALDAV_SOURCES resolved to an empty list.")

    configs = []
    for entry in entries:
        auth_type = entry.get("auth_type", "basic")
        base_required = ["id", "url"]
        if auth_type == "oauth2":
            required = base_required + ["client_id", "client_secret", "refresh_token"]
        elif auth_type == "basic":
            required = base_required + ["username", "password"]
        else:
            raise ConfigError(
                f"Unknown auth_type '{auth_type}' for source '{entry.get('id', '?')}' "
                "- must be 'basic' or 'oauth2'."
            )
        missing = [k for k in required if k not in entry]
        if missing:
            raise ConfigError(
                f"Calendar source missing required field(s) {missing}: "
                f"{ {k: v for k, v in entry.items() if k not in ('password', 'client_secret', 'refresh_token')} }"
            )
        configs.append(SourceConfig(
            id=entry["id"],
            name=entry.get("name", entry["id"]),
            url=entry["url"],
            auth_type=auth_type,
            username=entry.get("username"),
            password=entry.get("password"),
            client_id=entry.get("client_id"),
            client_secret=entry.get("client_secret"),
            refresh_token=entry.get("refresh_token"),
            calendar_path=entry.get("calendar_path"),
            writable=entry.get("writable", True),
            timezone=entry.get("timezone", os.environ.get("DEFAULT_TIMEZONE", "UTC")),
        ))
    return configs


def load_and_validate() -> tuple[dict[str, CalDAVSourceClient], dict[str, CircuitBreaker]]:
    """
    Builds a connected CalDAVSourceClient per configured source, each
    wrapped with a circuit breaker (task 16/17). Called once at app
    startup. Per FR-1: "fail gracefully if unreachable" — a single bad
    source logs an error and is excluded from the returned dict rather
    than crashing the whole container, so other configured sources
    still come up. If NO sources connect, raises ConfigError (nothing
    to serve).

    Returns (clients, breakers), both keyed by source_id — main.py
    holds breakers separately for /health reporting.
    """
    configs = _load_source_configs()
    clients: dict[str, CalDAVSourceClient] = {}
    breakers: dict[str, CircuitBreaker] = {}

    for cfg in configs:
        auth = None
        if cfg.auth_type == "oauth2":
            auth = google_oauth.GoogleOAuth2Auth(
                client_id=cfg.client_id,
                client_secret=cfg.client_secret,
                refresh_token=cfg.refresh_token,
            )
        client = CalDAVSourceClient(
            source_id=cfg.id,
            url=cfg.url,
            username=cfg.username,
            password=cfg.password,
            calendar_path=cfg.calendar_path,
            auth=auth,
            display_name=cfg.name,
        )
        try:
            client.connect()
            breaker = _wrap_with_breaker(client)
            clients[cfg.id] = client
            breakers[cfg.id] = breaker
            logger.info("caldav_source_connected", extra={"source_id": cfg.id})
        except CalDAVAuthError:
            logger.error(
                "caldav_source_auth_failed",
                extra={
                    "source_id": cfg.id,
                    "hint": "iCloud requires an app-specific password, not "
                            "the account password - see PRD Appendix A Step 1.",
                },
            )
        except CalDAVNotFoundError:
            logger.error(
                "caldav_source_calendar_not_found",
                extra={"source_id": cfg.id, "calendar_path": cfg.calendar_path},
            )
        except CalDAVUnavailableError as exc:
            logger.error(
                "caldav_source_unreachable",
                extra={"source_id": cfg.id, "error": str(exc)},
            )

    if not clients:
        raise ConfigError(
            "No calendar sources connected successfully. Check credentials "
            "and network reachability - see logs above for per-source errors."
        )

    return clients, breakers
