"""Configuration loading and validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kairos.settings import ConfigError, load_settings

TOKEN = "k" * 40
BASIC = {"id": "home", "url": "https://caldav.icloud.com", "username": "me@icloud.com", "password": "app-pw"}


def env(**kw: str) -> dict[str, str]:
    base = {"CALDAV_SOURCES": json.dumps([BASIC]), "KAIROS_API_TOKENS": TOKEN}
    base.update(kw)
    return base


def test_minimal() -> None:
    s = load_settings(env())
    assert [c.id for c in s.sources] == ["home"]
    assert s.sources[0].name == "home"
    assert s.sources[0].writable is True
    assert s.sources[0].timezone == "UTC"
    assert s.api_tokens == frozenset({TOKEN})
    assert s.oidc is None


def test_default_timezone_and_override() -> None:
    sources = [BASIC, {**BASIC, "id": "work", "timezone": "Europe/Madrid", "writable": False}]
    s = load_settings(env(CALDAV_SOURCES=json.dumps(sources), DEFAULT_TIMEZONE="America/New_York"))
    assert [c.timezone for c in s.sources] == ["America/New_York", "Europe/Madrid"]
    assert s.sources[1].writable is False


def test_yaml_file(tmp_path: Path) -> None:
    path = tmp_path / "sources.yaml"
    path.write_text(
        "sources:\n  - id: nc\n    url: https://nc.example/remote.php/dav\n    username: u\n    password: p\n"
    )
    s = load_settings({"KAIROS_SOURCES_FILE": str(path), "KAIROS_API_TOKENS": TOKEN})
    assert s.sources[0].id == "nc"


def test_secret_file(tmp_path: Path) -> None:
    secret = tmp_path / "tokens"
    secret.write_text(TOKEN + "\n")
    s = load_settings({"CALDAV_SOURCES": json.dumps([BASIC]), "KAIROS_API_TOKENS_FILE": str(secret)})
    assert s.api_tokens == frozenset({TOKEN})


def test_oauth_source() -> None:
    src = {
        "id": "g",
        "url": "https://apidata.googleusercontent.com/caldav/v2/x/events",
        "auth_type": "oauth2",
        "client_id": "c",
        "client_secret": "s",
        "refresh_token": "r",
    }
    assert load_settings(env(CALDAV_SOURCES=json.dumps([src]))).sources[0].auth_type == "oauth2"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"CALDAV_SOURCES": "not json"}, "valid JSON"),
        ({"CALDAV_SOURCES": "[]"}, "non-empty"),
        ({"CALDAV_SOURCES": json.dumps([{**BASIC, "password": ""}])}, "password"),
        ({"CALDAV_SOURCES": json.dumps([BASIC, BASIC])}, "Duplicate"),
        ({"CALDAV_SOURCES": json.dumps([{**BASIC, "auth_type": "digest"}])}, "auth_type"),
        ({"CALDAV_SOURCES": json.dumps([{**BASIC, "timezone": "Mars/Base"}])}, "timezone"),
        ({"DEFAULT_TIMEZONE": "Nope"}, "timezone"),
        ({"KAIROS_API_TOKENS": "short"}, "32 characters"),
        ({"KAIROS_OIDC_ISSUER": "https://id.example.com"}, "KAIROS_PUBLIC_URL"),
        ({"KAIROS_LOCK_WINDOW_MINUTES": "-1"}, "negative"),
    ],
)
def test_invalid(overrides: dict[str, str], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load_settings(env(**overrides))


def test_missing_sources_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="No CALDAV_SOURCES"):
        load_settings({"KAIROS_SOURCES_FILE": str(tmp_path / "missing.yaml")})


def test_errors_redact_secrets() -> None:
    entry = {"id": "x", "url": "u", "username": "me", "password": "hunter2", "client_secret": "abc"}
    del entry["url"]
    with pytest.raises(ConfigError) as info:
        load_settings(env(CALDAV_SOURCES=json.dumps([entry])))
    assert "hunter2" not in str(info.value)


def test_oidc() -> None:
    s = load_settings(
        env(
            KAIROS_PUBLIC_URL="https://cal.example.com/",
            KAIROS_OIDC_ISSUER="https://id.example.com/app/",
            KAIROS_OIDC_REQUIRED_SCOPES="calendar, openid",
            KAIROS_OIDC_ALLOWED_SUBJECTS="eric",
        )
    )
    assert s.public_url == "https://cal.example.com"
    assert s.mcp_resource_url == "https://cal.example.com/mcp"
    assert s.oidc is not None
    assert s.oidc.issuer == "https://id.example.com/app"
    assert s.oidc.audiences == ("https://cal.example.com/mcp",)
    assert s.oidc.required_scopes == ("calendar", "openid")
    assert s.oidc.allowed_subjects == ("eric",)


def test_repr_hides_tokens() -> None:
    assert TOKEN not in repr(load_settings(env()))
