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

logger = logging.getLogger("caldav_gateway.config")


@dataclass
class SourceConfig:
    id: str
    name: str
    url: str
    username: str
    password: str
    calendar_path: Optional[str] = None
    writable: bool = True
    timezone: str = "UTC"


class ConfigError(Exception):
    """Raised on malformed CALDAV_SOURCES or missing required fields."""


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
        missing = [k for k in ("id", "url", "username", "password") if k not in entry]
        if missing:
            raise ConfigError(
                f"Calendar source missing required field(s) {missing}: "
                f"{ {k: v for k, v in entry.items() if k != 'password'} }"
            )
        configs.append(SourceConfig(
            id=entry["id"],
            name=entry.get("name", entry["id"]),
            url=entry["url"],
            username=entry["username"],
            password=entry["password"],
            calendar_path=entry.get("calendar_path"),
            writable=entry.get("writable", True),
            timezone=entry.get("timezone", os.environ.get("DEFAULT_TIMEZONE", "UTC")),
        ))
    return configs


def load_and_validate() -> dict[str, CalDAVSourceClient]:
    """
    Builds a connected CalDAVSourceClient per configured source. Called
    once at app startup. Per FR-1: "fail gracefully if unreachable" — a
    single bad source logs an error and is excluded from the returned
    dict rather than crashing the whole container, so other configured
    sources still come up. If NO sources connect, raises ConfigError
    (nothing to serve).
    """
    configs = _load_source_configs()
    clients: dict[str, CalDAVSourceClient] = {}

    for cfg in configs:
        client = CalDAVSourceClient(
            source_id=cfg.id,
            url=cfg.url,
            username=cfg.username,
            password=cfg.password,
            calendar_path=cfg.calendar_path,
        )
        try:
            client.connect()
            clients[cfg.id] = client
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

    return clients
