"""Calendar providers: how Kairos builds a source for each kind of calendar service.

The engine ships one provider, ``caldav``, which covers any CalDAV server
(iCloud, Nextcloud, Fastmail, Google over OAuth). Other packages add
providers through the ``kairos.providers`` entry point group. Each entry
point names a :class:`Provider`, or a function with no arguments that
returns one, and its name must match the provider's name::

    [project.entry-points."kairos.providers"]
    zoho = "kairos_provider_zoho:provider"

A calendar source picks its provider with the ``provider`` field and
defaults to ``caldav``, so existing configurations keep working.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from importlib.metadata import EntryPoint, entry_points
from typing import TYPE_CHECKING, Literal

from kairos.settings import ConfigError, SourceConfig

if TYPE_CHECKING:
    from kairos.service import Source

logger = logging.getLogger("kairos.providers")

ENTRY_POINT_GROUP = "kairos.providers"
DEFAULT_PROVIDER = "caldav"
API_VERSION = 1

AuthType = Literal["basic", "oauth2"]


@dataclass(frozen=True)
class Provider:
    """One kind of calendar service.

    build_source turns a SourceConfig into an object that satisfies
    kairos.service.Source. two_way says whether the service accepts writes;
    a provider with two_way=False makes every one of its calendars read-only.
    """

    name: str
    display_name: str
    build_source: Callable[[SourceConfig], Source]
    auth_types: tuple[AuthType, ...] = ("basic",)
    default_url: str | None = None
    two_way: bool = True
    api_version: int = API_VERSION


def _build_caldav(config: SourceConfig) -> Source:
    from kairos.sources.caldav_source import CalDAVSource

    return CalDAVSource(config)


CALDAV = Provider(
    name=DEFAULT_PROVIDER,
    display_name="CalDAV",
    build_source=_build_caldav,
    auth_types=("basic", "oauth2"),
)


class ProviderRegistry:
    """The providers this process can build sources with."""

    def __init__(self, providers: Iterable[Provider] = ()) -> None:
        self._providers: dict[str, Provider] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: Provider) -> None:
        if not isinstance(provider, Provider):
            raise TypeError(f"Expected a kairos.providers.Provider, got {type(provider).__name__}")
        if provider.api_version != API_VERSION:
            raise ConfigError(
                f"Provider {provider.name!r} targets plugin API {provider.api_version}; "
                f"this Kairos supports {API_VERSION}"
            )
        if provider.name in self._providers:
            raise ConfigError(f"Provider {provider.name!r} is registered twice")
        self._providers[provider.name] = provider

    def __contains__(self, name: object) -> bool:
        return name in self._providers

    def names(self) -> list[str]:
        return sorted(self._providers)

    def get(self, name: str) -> Provider:
        try:
            return self._providers[name]
        except KeyError:
            raise ConfigError(
                f"Unknown calendar provider {name!r}; installed providers: {', '.join(self.names())}"
            ) from None

    def source_factory(self, config: SourceConfig) -> Source:
        """Build the source for one calendar. Pass this to CalendarService."""
        provider = self.get(config.provider)
        if config.auth_type not in provider.auth_types:
            raise ConfigError(
                f"Source {config.id!r}: provider {provider.name!r} doesn't support auth_type {config.auth_type!r}"
            )
        return provider.build_source(config)


def _load(ep: EntryPoint) -> Provider:
    obj = ep.load()
    if not isinstance(obj, Provider) and callable(obj):
        obj = obj()
    if not isinstance(obj, Provider):
        raise TypeError(f"entry point {ep.value!r} gave {type(obj).__name__}, not a Provider")
    if obj.name != ep.name:
        raise ConfigError(f"entry point {ep.name!r} returned provider {obj.name!r}; the names must match")
    return obj


def load_providers(eps: Iterable[EntryPoint] | None = None) -> ProviderRegistry:
    """The built-in caldav provider plus every installed plugin.

    A plugin that fails to load is logged and skipped, so one broken package
    can't take down calendars that don't use it. A source that names a
    skipped provider fails at startup with an unknown-provider error.
    """
    registry = ProviderRegistry([CALDAV])
    found = entry_points(group=ENTRY_POINT_GROUP) if eps is None else eps
    for ep in found:
        try:
            registry.register(_load(ep))
        except Exception as exc:
            logger.error("provider_load_failed", extra={"provider": ep.name, "error": str(exc)})
    return registry
