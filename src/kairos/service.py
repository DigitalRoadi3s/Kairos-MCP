"""The calendar core. Both the REST and MCP adapters call this and nothing else.

Responsibilities: own the configured sources, enforce the write rules
(read-only calendars, the lock window, recurring-event limits), run every
blocking CalDAV call in a worker thread behind a circuit breaker, and keep
the daily-brief cache coherent with writes.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, TypeVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import anyio
import anyio.to_thread

from kairos import brief as brief_mod
from kairos import ical
from kairos.errors import (
    CalendarNotFoundError,
    EventLockedError,
    InvalidRequestError,
    KairosError,
    ReadOnlyCalendarError,
    SourceUnavailableError,
)
from kairos.models import CalendarInfo, Event, EventCreate, EventPatch
from kairos.observability import caldav_calendar_sync_errors_total, caldav_request_duration_seconds
from kairos.resilience import CircuitBreaker
from kairos.settings import Settings, SourceConfig
from kairos.sources.caldav_source import CalDAVSource

logger = logging.getLogger("kairos.service")

T = TypeVar("T")
MAX_RANGE_DAYS = 366
DEFAULT_RANGE_DAYS = 30


class Source(Protocol):
    """What the service needs from a calendar backend. CalDAVSource implements it."""

    id: str
    config: SourceConfig

    @property
    def connected(self) -> bool: ...
    def connect(self) -> None: ...
    def close(self) -> None: ...
    def list_events(self, start: datetime, end: datetime) -> list[Event]: ...
    def get_event(self, uid: str) -> Event: ...
    def create_event(self, ical_text: str) -> Event: ...
    def update_event(self, uid: str, patch: EventPatch) -> Event: ...
    def delete_event(self, uid: str) -> None: ...


@dataclass
class _Handle:
    source: Source
    breaker: CircuitBreaker
    info: CalendarInfo
    last_success: float | None = None


def event_to_dict(e: Event, tz: ZoneInfo | None = None) -> dict[str, Any]:
    def ts(value: datetime | None) -> str | None:
        if value is None:
            return None
        return (value.astimezone(tz) if tz else value).isoformat()

    return {
        "uid": e.uid,
        "title": e.title,
        "description": e.description,
        "start_time": ts(e.start_time),
        "end_time": ts(e.end_time),
        "all_day": e.all_day,
        "attendees": [{"email": a.email, "name": a.name, "status": a.status} for a in e.attendees],
        "location": e.location,
        "recurrence_rule": e.recurrence_rule,
        "recurring": e.recurring,
        "recurrence_id": ts(e.recurrence_id),
        "last_modified": ts(e.last_modified),
        "status": e.status,
        "timezone": e.original_timezone,
    }


def parse_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise InvalidRequestError(f"Unknown timezone {name!r}; use an IANA name like America/New_York") from exc


class CalendarService:
    def __init__(
        self,
        settings: Settings,
        source_factory: Callable[[SourceConfig], Source] = CalDAVSource,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        breaker_factory: Callable[[str], CircuitBreaker] = CircuitBreaker,
    ) -> None:
        self.settings = settings
        self._now = clock
        self.brief_cache = brief_mod.BriefCache()
        self._handles: dict[str, _Handle] = {}
        for cfg in settings.sources:
            self._handles[cfg.id] = _Handle(
                source=source_factory(cfg),
                breaker=breaker_factory(cfg.id),
                info=CalendarInfo(id=cfg.id, name=cfg.name, writable=cfg.writable, timezone=cfg.timezone),
            )

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        """Connect every source. A source that fails stays registered and reconnects on first use."""

        async def connect(handle: _Handle) -> None:
            try:
                await self._call(handle, "connect", handle.source.connect, retry=False)
            except KairosError as exc:
                logger.error("caldav_source_connect_failed", extra={"source_id": handle.info.id, "error": str(exc)})

        async with anyio.create_task_group() as tg:
            for handle in self._handles.values():
                tg.start_soon(connect, handle)

    async def close(self) -> None:
        for handle in self._handles.values():
            await anyio.to_thread.run_sync(handle.source.close)

    # ------------------------------------------------------------------ plumbing

    def _handle(self, calendar_id: str) -> _Handle:
        try:
            return self._handles[calendar_id]
        except KeyError:
            raise CalendarNotFoundError(f"Calendar {calendar_id!r} not found") from None

    async def _call(self, handle: _Handle, op: str, fn: Callable[..., T], *args: Any, retry: bool = True) -> T:
        async def attempt() -> T:
            with caldav_request_duration_seconds.labels(method=op, path=handle.info.id).time():
                return await anyio.to_thread.run_sync(lambda: fn(*args))

        try:
            result = await handle.breaker.call(attempt, retry=retry)
        except SourceUnavailableError:
            caldav_calendar_sync_errors_total.labels(calendar_id=handle.info.id).inc()
            raise
        handle.last_success = time.time()
        return result

    def _writable(self, handle: _Handle) -> None:
        if not handle.info.writable:
            raise ReadOnlyCalendarError(f"Calendar {handle.info.id!r} is read-only")

    def _check_lock(self, event: Event, action: str) -> None:
        """One-off events can't be changed once they're within the lock window or already started.

        A recurring series is not locked by its first occurrence's date: renaming a
        standup that began last month is normal. Its times can't be changed at all.
        """
        if event.recurring:
            return
        window = timedelta(minutes=self.settings.lock_window_minutes)
        if event.start_time <= self._now() + window:
            raise EventLockedError(
                f"Event starts within {self.settings.lock_window_minutes} minutes or has already "
                f"started, so it can't be {action}"
            )

    # ------------------------------------------------------------------ reads

    def calendars(self) -> list[CalendarInfo]:
        return [h.info for h in self._handles.values()]

    def calendar(self, calendar_id: str) -> CalendarInfo:
        return self._handle(calendar_id).info

    async def list_events(
        self,
        calendar_id: str,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100,
    ) -> tuple[list[Event], int]:
        handle = self._handle(calendar_id)
        start = start or self._now()
        end = end or start + timedelta(days=DEFAULT_RANGE_DAYS)
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        if end.tzinfo is None:
            end = end.replace(tzinfo=UTC)
        if start >= end:
            raise InvalidRequestError("date_min must be before date_max")
        if end - start > timedelta(days=MAX_RANGE_DAYS):
            raise InvalidRequestError(f"Date range can't exceed {MAX_RANGE_DAYS} days")
        events = await self._call(handle, "list_events", handle.source.list_events, start, end)
        return events[:limit], len(events)

    async def get_event(self, calendar_id: str, uid: str) -> Event:
        handle = self._handle(calendar_id)
        return await self._call(handle, "get_event", handle.source.get_event, uid)

    async def daily_brief(self, calendar_id: str, tz_name: str | None = None) -> dict[str, Any]:
        handle = self._handle(calendar_id)
        tz = parse_timezone(tz_name or handle.info.timezone)
        day_start, day_end = brief_mod.day_bounds(tz, self._now())
        key = (calendar_id, tz.key, day_start.date())
        cached = self.brief_cache.get(key)
        if cached is not None:
            return cached
        try:
            events = await self._call(
                handle,
                "list_events",
                handle.source.list_events,
                day_start.astimezone(UTC),
                day_end.astimezone(UTC),
            )
        except SourceUnavailableError:
            stale = self.brief_cache.get(key, allow_stale=True)
            if stale is not None:
                return {**stale, "stale": True}
            raise
        result = brief_mod.compute_brief(
            events,
            calendar_id=calendar_id,
            calendar_name=handle.info.name,
            tz=tz,
            day_start=day_start,
            day_end=day_end,
        )
        self.brief_cache.set(key, result)
        return result

    # ------------------------------------------------------------------ writes

    async def create_event(self, calendar_id: str, body: EventCreate) -> Event:
        handle = self._handle(calendar_id)
        self._writable(handle)
        _, text = ical.build_ical(body, handle.info.timezone)
        event = await self._call(handle, "create_event", handle.source.create_event, text, retry=False)
        self.brief_cache.invalidate(calendar_id)
        return event

    async def update_event(self, calendar_id: str, uid: str, patch: EventPatch) -> Event:
        handle = self._handle(calendar_id)
        self._writable(handle)
        if not patch.model_fields_set:
            raise InvalidRequestError("Nothing to update")
        existing = await self._call(handle, "get_event", handle.source.get_event, uid)
        self._check_lock(existing, "modified")
        if existing.recurring and ({"start_time", "end_time", "all_day"} & patch.model_fields_set):
            raise EventLockedError(
                "Times of a recurring event can't be changed; delete the series and create a new one"
            )
        event = await self._call(handle, "update_event", handle.source.update_event, uid, patch, retry=False)
        self.brief_cache.invalidate(calendar_id)
        return event

    async def delete_event(self, calendar_id: str, uid: str) -> None:
        handle = self._handle(calendar_id)
        self._writable(handle)
        existing = await self._call(handle, "get_event", handle.source.get_event, uid)
        self._check_lock(existing, "deleted")
        await self._call(handle, "delete_event", handle.source.delete_event, uid, retry=False)
        self.brief_cache.invalidate(calendar_id)

    # ------------------------------------------------------------------ health

    def health(self) -> dict[str, Any]:
        calendars: dict[str, Any] = {}
        any_up = False
        for cid, h in self._handles.items():
            breaker = h.breaker.status()
            if h.source.connected and breaker["state"] == "closed":
                status = "connected"
                any_up = True
            elif breaker["state"] != "closed":
                status = "degraded"
            else:
                status = "disconnected"
            calendars[cid] = {
                "status": status,
                "last_success": (
                    datetime.fromtimestamp(h.last_success, tz=UTC).isoformat() if h.last_success else None
                ),
                "last_error": breaker["last_error"],
                "circuit_breaker": {"state": breaker["state"], "recent_failures": breaker["recent_failures"]},
            }
        return {"status": "healthy" if any_up else "unhealthy", "calendars": calendars}
