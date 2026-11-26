"""Shared fixtures: an in-memory calendar source that stores real iCalendar text.

FakeSource goes through the same ical module as CalDAVSource, so the REST
and MCP tests exercise real parsing, generation and in-place patching. Only
the network is faked. tests/integration runs the real client against Radicale.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import recurring_ical_events
from fastapi.testclient import TestClient
from icalendar import Calendar

from kairos import ical
from kairos.app import create_app
from kairos.errors import EventNotFoundError, SourceUnavailableError
from kairos.models import Event, EventPatch
from kairos.resilience import CircuitBreaker
from kairos.service import CalendarService
from kairos.settings import Settings, SourceConfig

TOKEN = "t" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class FakeSource:
    def __init__(self, config: SourceConfig) -> None:
        self.config = config
        self.id = config.id
        self.tz = ZoneInfo(config.timezone)
        self.store: dict[str, str] = {}
        self.fail_with: Exception | None = None
        self.calls: list[str] = []
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    def _maybe_fail(self, op: str) -> None:
        self.calls.append(op)
        if self.fail_with is not None:
            raise self.fail_with

    def connect(self) -> None:
        self._maybe_fail("connect")
        self._connected = True

    def close(self) -> None:
        self._connected = False

    def put_raw(self, text: str) -> str:
        event = ical.parse_ical(text, self.tz)
        self.store[event.uid] = text
        return event.uid

    def list_events(self, start: datetime, end: datetime) -> list[Event]:
        self._maybe_fail("list_events")
        out: list[Event] = []
        for text in self.store.values():
            cal = Calendar.from_ical(text)
            for component in recurring_ical_events.of(cal).between(start, end):
                out.append(ical.parse_component(component, self.tz))
        return sorted(out, key=lambda e: e.start_time)

    def get_event(self, uid: str) -> Event:
        self._maybe_fail("get_event")
        if uid not in self.store:
            raise EventNotFoundError(f"Event {uid!r} not found")
        return ical.parse_ical(self.store[uid], self.tz)

    def create_event(self, ical_text: str) -> Event:
        self._maybe_fail("create_event")
        self.put_raw(ical_text)
        return ical.parse_ical(ical_text, self.tz)

    def update_event(self, uid: str, patch: EventPatch) -> Event:
        self._maybe_fail("update_event")
        if uid not in self.store:
            raise EventNotFoundError(f"Event {uid!r} not found")
        cal = Calendar.from_ical(self.store[uid])
        vevent = ical.apply_patch(cal, patch, self.config.timezone)
        self.store[uid] = cal.to_ical().decode()
        return ical.parse_component(vevent, self.tz)

    def delete_event(self, uid: str) -> None:
        self._maybe_fail("delete_event")
        if uid not in self.store:
            raise EventNotFoundError(f"Event {uid!r} not found")
        del self.store[uid]


def make_settings(**overrides: Any) -> Settings:
    sources = overrides.pop(
        "sources",
        (
            SourceConfig(
                id="home",
                name="Home",
                url="https://dav.example",
                username="u",
                password="p",
                timezone="America/New_York",
            ),
            SourceConfig(id="work", name="Work", url="https://dav.example", username="u", password="p", writable=False),
        ),
    )
    base: dict[str, Any] = {"sources": sources, "api_tokens": frozenset({TOKEN})}
    base.update(overrides)
    return Settings(**base)


def fast_breaker(name: str) -> CircuitBreaker:
    return CircuitBreaker(name, backoff_base=0.0)


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def service(settings: Settings) -> CalendarService:
    return CalendarService(settings, FakeSource, breaker_factory=fast_breaker)


@pytest.fixture
def home(service: CalendarService) -> FakeSource:
    return service._handles["home"].source  # type: ignore[return-value]


@pytest.fixture
def client(settings: Settings, service: CalendarService) -> Iterator[TestClient]:
    with TestClient(create_app(settings, service)) as c:
        yield c


def future(days: float = 2, hours: float = 0) -> datetime:
    return (datetime.now(UTC) + timedelta(days=days, hours=hours)).replace(microsecond=0)


def unavailable() -> SourceUnavailableError:
    return SourceUnavailableError("down")
