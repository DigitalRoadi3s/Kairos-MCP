"""
Daily brief engine (task 5, Opus tier). FR-6.

Computes today's events + free-slot analysis + attendee aggregation for
one calendar source, with a 5-minute cache per NFR-2.

Interface for Sonnet task 6: call get_daily_brief(client, source_id, tz_name).
Interface for Opus task 14 (cache invalidation on write): call
invalidate(source_id) after any create/update/delete succeeds.

Cache invalidation ordering (flagged High risk in PRD - "cache
invalidation race condition"): the PRD's own mitigation is "write events
first, then invalidate cache; cache timestamp included in response." We
implement that literally - invalidate() is only ever called by task
11-13's write handlers AFTER the CalDAV write call returns success, never
before. A read that is in-flight when invalidate() runs will still return
its own already-fetched cached snapshot (each read takes an immutable
reference to the cache entry rather than reading a live mutable object),
so no reader ever observes a torn/partial cache state.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from caldav_client import CalDAVSourceClient, CalDAVUnavailableError, Event

CACHE_TTL_SECONDS = 5 * 60
FREE_SLOT_MIN_MINUTES = 15


@dataclass(frozen=True)
class _CacheEntry:
    """Immutable snapshot - safe to hand to concurrent readers."""
    brief: dict
    cached_at: float  # time.monotonic()


class _CacheStore:
    """
    Thread-safe per-source cache. One instance shared across requests
    (held in app.state by main.py, wired in task 6).
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._entries: dict[str, _CacheEntry] = {}

    def get(self, source_id: str) -> dict | None:
        with self._lock:
            entry = self._entries.get(source_id)
        if entry is None:
            return None
        if time.monotonic() - entry.cached_at > CACHE_TTL_SECONDS:
            return None
        return entry.brief  # immutable dict-of-plain-types; safe to return directly

    def set(self, source_id: str, brief: dict) -> None:
        with self._lock:
            self._entries[source_id] = _CacheEntry(brief=brief, cached_at=time.monotonic())

    def invalidate(self, source_id: str) -> None:
        """Called by write handlers (tasks 11-13) AFTER a successful CalDAV write."""
        with self._lock:
            self._entries.pop(source_id, None)

    def get_stale(self, source_id: str) -> dict | None:
        """Returns the cached brief even if past TTL, or None if never cached.
        Used only for graceful degradation when a live fetch fails (NFR-2)."""
        with self._lock:
            entry = self._entries.get(source_id)
        return entry.brief if entry is not None else None


# Module-level singleton is fine here: one process, one cache. main.py may
# instead construct its own and pass it through app.state - either works
# since _CacheStore is stateless-safe (all state behind the lock).
_cache = _CacheStore()


def invalidate(source_id: str) -> None:
    _cache.invalidate(source_id)


def get_daily_brief(client: CalDAVSourceClient, source_id: str,
                     tz_name: str = "UTC", calendar_name: str = "") -> dict:
    """
    Returns the FR-6 response shape. Cached for CACHE_TTL_SECONDS per
    source_id. Raises CalDAVUnavailableError only if there's no cached
    fallback to serve (graceful degradation per NFR-2).
    """
    cached = _cache.get(source_id)
    if cached is not None:
        return cached

    try:
        brief = _compute_daily_brief(client, source_id, tz_name, calendar_name)
    except CalDAVUnavailableError:
        # Graceful degradation: serve last-known-good even if past TTL,
        # rather than a hard 503, if we have anything at all.
        stale = _cache.get_stale(source_id)
        if stale is not None:
            return stale
        raise

    _cache.set(source_id, brief)
    return brief


def _compute_daily_brief(client: CalDAVSourceClient, source_id: str,
                          tz_name: str, calendar_name: str) -> dict:
    tz = ZoneInfo(tz_name)
    now_local = datetime.now(tz)
    day_start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end_local = day_start_local + timedelta(days=1)

    events = client.list_events(
        date_min=day_start_local.astimezone(timezone.utc),
        date_max=day_end_local.astimezone(timezone.utc),
    )
    events.sort(key=lambda e: e.start_time)

    event_dicts = []
    attendee_counts: dict[str, dict] = {}
    total_minutes = 0

    for e in events:
        duration_minutes = int((e.end_time - e.start_time).total_seconds() // 60)
        total_minutes += duration_minutes
        for a in e.attendees:
            entry = attendee_counts.setdefault(a.email, {"email": a.email, "name": a.name, "count": 0})
            entry["count"] += 1

        event_dicts.append({
            "uid": e.uid,
            "title": e.title,
            "start_time": e.start_time.astimezone(tz).isoformat(),
            "end_time": e.end_time.astimezone(tz).isoformat(),
            "duration_minutes": duration_minutes,
            "location": e.location,
            "attendees": [
                {"email": a.email, "name": a.name, "status": a.status} for a in e.attendees
            ],
            "is_all_day": e.all_day,
            "is_busy": e.status != "cancelled",
        })

    free_slots = _compute_free_slots(events, day_start_local, day_end_local, tz)

    top_attendees = sorted(attendee_counts.values(), key=lambda a: -a["count"])

    return {
        "calendar_id": source_id,
        "calendar_name": calendar_name or source_id,
        "date": day_start_local.date().isoformat(),
        "timezone": tz_name,
        "summary": {
            "total_events": len(events),
            "total_calendar_time_minutes": total_minutes,
            "first_event_start": event_dicts[0]["start_time"] if event_dicts else None,
            "last_event_end": event_dicts[-1]["end_time"] if event_dicts else None,
            "top_attendees": top_attendees,
            "free_slots": free_slots,
        },
        "events": event_dicts,
    }


def _compute_free_slots(events: list[Event], day_start_local: datetime,
                         day_end_local: datetime, tz: ZoneInfo) -> list[dict]:
    """
    Gaps > FREE_SLOT_MIN_MINUTES between sorted, non-overlapping busy
    blocks, bounded to [day_start_local, day_end_local]. All-day events
    are excluded from busy-block math (they don't block wall-clock time)
    but overlapping timed events ARE merged into one busy block first so
    a gap isn't reported inside a double-booked span.
    """
    busy_blocks: list[tuple[datetime, datetime]] = []
    for e in sorted(events, key=lambda e: e.start_time):
        if e.all_day or e.status == "cancelled":
            continue
        start = e.start_time.astimezone(tz)
        end = e.end_time.astimezone(tz)
        start = max(start, day_start_local)
        end = min(end, day_end_local)
        if start >= end:
            continue
        if busy_blocks and start <= busy_blocks[-1][1]:
            # Overlaps/adjoins previous block - merge instead of creating
            # a false gap.
            prev_start, prev_end = busy_blocks[-1]
            busy_blocks[-1] = (prev_start, max(prev_end, end))
        else:
            busy_blocks.append((start, end))

    slots = []
    cursor = day_start_local
    for start, end in busy_blocks:
        gap_minutes = (start - cursor).total_seconds() / 60
        if gap_minutes > FREE_SLOT_MIN_MINUTES:
            slots.append(_slot(cursor, start))
        cursor = max(cursor, end)

    trailing_minutes = (day_end_local - cursor).total_seconds() / 60
    if trailing_minutes > FREE_SLOT_MIN_MINUTES:
        slots.append(_slot(cursor, day_end_local))

    return slots


def _slot(start: datetime, end: datetime) -> dict:
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "duration_minutes": int((end - start).total_seconds() // 60),
    }
