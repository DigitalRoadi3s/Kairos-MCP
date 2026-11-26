"""Runtime configuration, read from the environment once at startup.

Calendar sources keep the 1.x format: a JSON array in CALDAV_SOURCES, or a
`sources:` list in a YAML file. Any secret-bearing variable can instead be
given as a file path in the matching *_FILE variable (Docker secrets).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

DEFAULT_SOURCES_FILE = "/config/sources.yaml"
_SECRET_KEYS = ("password", "client_secret", "refresh_token")


class ConfigError(Exception):
    """Raised on malformed or missing configuration."""


@dataclass(frozen=True)
class SourceConfig:
    id: str
    name: str
    url: str
    auth_type: Literal["basic", "oauth2"] = "basic"
    username: str | None = None
    password: str | None = field(default=None, repr=False)
    client_id: str | None = None
    client_secret: str | None = field(default=None, repr=False)
    refresh_token: str | None = field(default=None, repr=False)
    calendar_path: str | None = None
    writable: bool = True
    timezone: str = "UTC"
    timeout: int = 30


@dataclass(frozen=True)
class OIDCConfig:
    """An external OAuth 2.0 / OIDC authorization server that issues JWT access tokens for /mcp."""

    issuer: str
    audiences: tuple[str, ...]
    required_scopes: tuple[str, ...] = ()
    jwks_uri: str | None = None  # discovered from the issuer when unset
    allowed_subjects: tuple[str, ...] = ()  # empty = any subject the issuer vouches for


@dataclass(frozen=True)
class Settings:
    sources: tuple[SourceConfig, ...]
    api_tokens: frozenset[str] = field(default_factory=frozenset, repr=False)
    insecure_no_auth: bool = False
    public_url: str | None = None
    oidc: OIDCConfig | None = None
    allowed_hosts: tuple[str, ...] = ()
    lock_window_minutes: int = 15
    log_level: str = "info"
    host: str = "0.0.0.0"  # noqa: S104 - a container listens on all interfaces
    port: int = 8080

    @property
    def mcp_resource_url(self) -> str | None:
        return f"{self.public_url.rstrip('/')}/mcp" if self.public_url else None


def _env(name: str, environ: dict[str, str]) -> str | None:
    """Value of NAME, or the contents of the file named by NAME_FILE."""
    value = environ.get(name)
    if value:
        return value
    path = environ.get(f"{name}_FILE")
    if path:
        try:
            return Path(path).read_text().strip()
        except OSError as exc:
            raise ConfigError(f"{name}_FILE points at {path!r}, which can't be read: {exc}") from exc
    return None


def _split(value: str | None) -> tuple[str, ...]:
    return tuple(v.strip() for v in (value or "").replace("\n", ",").split(",") if v.strip())


def _bool(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _check_timezone(name: str, where: str) -> str:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(f"{where}: unknown timezone {name!r}") from exc
    return name


def _raw_source_entries(environ: dict[str, str]) -> list[dict[str, Any]]:
    raw = _env("CALDAV_SOURCES", environ)
    if raw:
        try:
            entries = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"CALDAV_SOURCES is not valid JSON: {exc}") from exc
    else:
        path = Path(environ.get("KAIROS_SOURCES_FILE", DEFAULT_SOURCES_FILE))
        if not path.exists():
            raise ConfigError(
                f"No CALDAV_SOURCES and no sources file at {path}. At least one calendar source is required."
            )
        data = yaml.safe_load(path.read_text()) or {}
        entries = data.get("sources", []) if isinstance(data, dict) else data
    if not isinstance(entries, list) or not entries:
        raise ConfigError("Calendar sources must be a non-empty list.")
    return entries


def _redacted(entry: dict[str, Any]) -> dict[str, Any]:
    return {k: ("***" if k in _SECRET_KEYS else v) for k, v in entry.items()}


def parse_sources(entries: list[dict[str, Any]], default_tz: str) -> tuple[SourceConfig, ...]:
    configs: list[SourceConfig] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ConfigError(f"Each calendar source must be an object, got {type(entry).__name__}")
        auth_type = entry.get("auth_type", "basic")
        if auth_type == "oauth2":
            required = ["id", "url", "client_id", "client_secret", "refresh_token"]
        elif auth_type == "basic":
            required = ["id", "url", "username", "password"]
        else:
            raise ConfigError(
                f"Unknown auth_type {auth_type!r} for source {entry.get('id', '?')!r}; use 'basic' or 'oauth2'."
            )
        missing = [k for k in required if not entry.get(k)]
        if missing:
            raise ConfigError(f"Calendar source missing required field(s) {missing}: {_redacted(entry)}")
        source_id = str(entry["id"])
        if source_id in seen:
            raise ConfigError(f"Duplicate calendar source id {source_id!r}")
        seen.add(source_id)
        configs.append(
            SourceConfig(
                id=source_id,
                name=str(entry.get("name") or source_id),
                url=str(entry["url"]),
                auth_type=auth_type,
                username=entry.get("username"),
                password=entry.get("password"),
                client_id=entry.get("client_id"),
                client_secret=entry.get("client_secret"),
                refresh_token=entry.get("refresh_token"),
                calendar_path=entry.get("calendar_path"),
                writable=bool(entry.get("writable", True)),
                timezone=_check_timezone(str(entry.get("timezone") or default_tz), f"source {source_id!r}"),
                timeout=int(entry.get("timeout", 30)),
            )
        )
    return tuple(configs)


def load_settings(environ: dict[str, str] | None = None) -> Settings:
    env = dict(os.environ if environ is None else environ)
    default_tz = _check_timezone(env.get("DEFAULT_TIMEZONE", "UTC"), "DEFAULT_TIMEZONE")
    sources = parse_sources(_raw_source_entries(env), default_tz)

    public_url = (env.get("KAIROS_PUBLIC_URL") or "").rstrip("/") or None
    oidc = None
    issuer = env.get("KAIROS_OIDC_ISSUER")
    if issuer:
        if not public_url:
            raise ConfigError("KAIROS_OIDC_ISSUER needs KAIROS_PUBLIC_URL (the https URL clients use).")
        audiences = _split(env.get("KAIROS_OIDC_AUDIENCE")) or (f"{public_url}/mcp",)
        oidc = OIDCConfig(
            issuer=issuer.rstrip("/"),
            audiences=audiences,
            required_scopes=_split(env.get("KAIROS_OIDC_REQUIRED_SCOPES")),
            jwks_uri=env.get("KAIROS_OIDC_JWKS_URI") or None,
            allowed_subjects=_split(env.get("KAIROS_OIDC_ALLOWED_SUBJECTS")),
        )

    tokens = frozenset(_split(_env("KAIROS_API_TOKENS", env)))
    weak = [t for t in tokens if len(t) < 32]
    if weak:
        raise ConfigError("Every KAIROS_API_TOKENS entry must be at least 32 characters.")

    lock = int(env.get("KAIROS_LOCK_WINDOW_MINUTES", "15"))
    if lock < 0:
        raise ConfigError("KAIROS_LOCK_WINDOW_MINUTES can't be negative.")

    return Settings(
        sources=sources,
        api_tokens=tokens,
        insecure_no_auth=_bool(env.get("KAIROS_INSECURE_NO_AUTH")),
        public_url=public_url,
        oidc=oidc,
        allowed_hosts=_split(env.get("KAIROS_ALLOWED_HOSTS")),
        lock_window_minutes=lock,
        log_level=env.get("LOG_LEVEL", "info"),
        host=env.get("KAIROS_HOST", "0.0.0.0"),  # noqa: S104
        port=int(env.get("KAIROS_PORT", "8080")),
    )
