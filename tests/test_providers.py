"""Provider plugins: loading from entry points, the registry and the service using it."""

from __future__ import annotations

from importlib.metadata import EntryPoint
from typing import Any

import pytest

from kairos import service as service_mod
from kairos.providers import CALDAV, Provider, ProviderRegistry, load_providers
from kairos.service import CalendarService
from kairos.settings import ConfigError, SourceConfig, parse_sources

from .conftest import FakeSource, fast_breaker, make_settings

GROUP = "kairos.providers"

READ_ONLY = Provider(name="feed", display_name="Feed", build_source=FakeSource, two_way=False)
ZOHO = Provider(name="zoho", display_name="Zoho Calendar", build_source=FakeSource, default_url="https://z.example")
NOT_A_PROVIDER = object()


def make_zoho() -> Provider:
    return ZOHO


def broken() -> Provider:
    raise RuntimeError("plugin bug")


def ep(name: str, attr: str) -> EntryPoint:
    return EntryPoint(name=name, value=f"tests.test_providers:{attr}", group=GROUP)


def cfg(**kw: Any) -> SourceConfig:
    base: dict[str, Any] = {"id": "c", "name": "C", "url": "https://dav.example", "username": "u", "password": "p"}
    base.update(kw)
    return SourceConfig(**base)


def test_caldav_is_built_in() -> None:
    registry = load_providers([])
    assert registry.names() == ["caldav"]
    assert registry.get("caldav") is CALDAV


def test_loads_provider_objects_and_factories() -> None:
    registry = load_providers([ep("zoho", "make_zoho"), ep("feed", "READ_ONLY")])
    assert registry.names() == ["caldav", "feed", "zoho"]
    assert isinstance(registry.source_factory(cfg(provider="zoho")), FakeSource)


@pytest.mark.parametrize(
    "entry",
    [
        ep("zoho", "broken"),  # raises
        ep("zoho", "NOT_A_PROVIDER"),  # wrong type
        ep("zoho", "READ_ONLY"),  # name mismatch
        EntryPoint(name="zoho", value="no_such_module_xyz:provider", group=GROUP),  # missing package
        ep("caldav", "CALDAV_COPY"),  # clashes with the built-in
    ],
)
def test_bad_plugin_is_skipped(entry: EntryPoint, caplog: pytest.LogCaptureFixture) -> None:
    registry = load_providers([entry])
    assert registry.names() == ["caldav"]
    assert "provider_load_failed" in caplog.text


CALDAV_COPY = Provider(name="caldav", display_name="Impostor", build_source=FakeSource)


def test_unknown_provider_names_installed_ones() -> None:
    with pytest.raises(ConfigError, match="installed providers: caldav"):
        ProviderRegistry([CALDAV]).source_factory(cfg(provider="nope"))


def test_auth_type_must_be_supported() -> None:
    registry = ProviderRegistry([ZOHO])
    with pytest.raises(ConfigError, match="doesn't support auth_type 'oauth2'"):
        registry.source_factory(cfg(provider="zoho", auth_type="oauth2"))


def test_wrong_api_version_rejected() -> None:
    future_api = Provider(name="x", display_name="X", build_source=FakeSource, api_version=99)
    with pytest.raises(ConfigError, match="plugin API 99"):
        ProviderRegistry([future_api])


def test_sources_default_to_caldav() -> None:
    entries = [{"id": "a", "url": "https://a", "username": "u", "password": "p"}]
    assert parse_sources(entries, "UTC")[0].provider == "caldav"
    entries[0]["provider"] = "zoho"
    assert parse_sources(entries, "UTC")[0].provider == "zoho"


def test_service_uses_installed_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service_mod, "load_providers", lambda: ProviderRegistry([CALDAV, ZOHO, READ_ONLY]))
    settings = make_settings(sources=(cfg(id="z", provider="zoho"), cfg(id="f", provider="feed")))
    svc = CalendarService(settings, breaker_factory=fast_breaker)
    assert isinstance(svc._handles["z"].source, FakeSource)
    assert svc.calendar("z").writable is True
    assert svc.calendar("f").writable is False  # a read-only provider wins over writable=True


def test_service_rejects_unknown_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service_mod, "load_providers", lambda: ProviderRegistry([CALDAV]))
    with pytest.raises(ConfigError, match="Unknown calendar provider 'zoho'"):
        CalendarService(make_settings(sources=(cfg(provider="zoho"),)))
