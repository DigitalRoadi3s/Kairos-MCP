"""Daily brief: today's events, free slots and who you're meeting.

Cached for five minutes per (calendar, time zone, local date). Writes
invalidate every entry for their calendar after the write succeeds. When a
live fetch fails, the last brief for the same key is served instead, marked
stale, rather than failing the request.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from kairos.models import Event

CACHE_TTL_SECONDS = 300.0
FREE_SLOT_MIN_MINUTES = 15

CacheKey = tuple[str, str, date]


@dataclass(frozen=True)
class _Entry:
    brief: dict[str, Any]
    cached_at: float


class BriefCache:
    def __init__(self, ttl: float = CACHE_TTL_SECONDS, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[CacheKey, _Entry] = {}

    def get(self, key: CacheKey, *, allow_stale: bool = False) -> dict[str, Any] | None:
        with self._lock:
            entry = self._entries.get(key)
        if entry is None:
            return None
        if not allow_stale and self._clock() - entry.cached_at > self._ttl:
            return None
        return entry.brief

    def set(self, key: CacheKey, brief: dict[str, Any]) -> None:
        with self._lock:
            self._entries[key] = _Entry(brief, self._clock())
            # Drop yesterday's entries so the cache can't grow without bound.
            cutoff = key[2] - timedelta(days=1)
            self._entries = {k: v for k, v in self._entries.items() if k[2] >= cutoff}

    def invalidate(self, calendar_id: str) -> None:
        with self._lock:
            self._entries = {k: v for k, v in self._entries.items() if k[0] != calendar_id}


def day_bounds(tz: ZoneInfo, now: datetime | None = None) -> tuple[datetime, datetime]:
    """Local midnight to the next local midnight, DST-safe."""
    local_now = (now or datetime.now(UTC)).astimezone(tz)
    start = datetime(local_now.year, local_now.month, local_now.day, tzinfo=tz)
    nxt = local_now.date() + timedelta(days=1)
    end = datetime(nxt.year, nxt.month, nxt.day, tzinfo=tz)
    return start, end


def _blocks_time(e: Event) -> bool:
    return not e.all_day and e.status != "cancelled" and not e.transparent


def compute_brief(
    events: list[Event],
    *,
    calendar_id: str,
    calendar_name: str,
    tz: ZoneInfo,
    day_start: datetime,
    day_end: datetime,
) -> dict[str, Any]:
    events = sorted(events, key=lambda e: e.start_time)
    event_dicts: list[dict[str, Any]] = []
    attendee_counts: dict[str, dict[str, Any]] = {}
    busy_minutes = 0

    for e in events:
        duration = int((e.end_time - e.start_time).total_seconds() // 60)
        if _blocks_time(e):
            clipped = min(e.end_time, day_end) - max(e.start_time, day_start)
            busy_minutes += max(0, int(clipped.total_seconds() // 60))
        for a in e.attendees:
            entry = attendee_counts.setdefault(a.email.lower(), {"email": a.email, "name": a.name, "count": 0})
            entry["count"] += 1
        event_dicts.append(
            {
                "uid": e.uid,
                "title": e.title,
                "start_time": e.start_time.astimezone(tz).isoformat(),
                "end_time": e.end_time.astimezone(tz).isoformat(),
                "duration_minutes": duration,
                "location": e.location,
                "attendees": [{"email": a.email, "name": a.name, "status": a.status} for a in e.attendees],
                "is_all_day": e.all_day,
                "is_busy": _blocks_time(e),
                "status": e.status,
            }
        )

    timed = [d for d, e in zip(event_dicts, events, strict=True) if not e.all_day]
    return {
        "calendar_id": calendar_id,
        "calendar_name": calendar_name,
        "date": day_start.date().isoformat(),
        "timezone": tz.key,
        "generated_at": datetime.now(UTC).isoformat(),
        "stale": False,
        "summary": {
            "total_events": len(events),
            "total_calendar_time_minutes": busy_minutes,
            "first_event_start": timed[0]["start_time"] if timed else None,
            "last_event_end": max((d["end_time"] for d in timed), default=None),
            "top_attendees": sorted(attendee_counts.values(), key=lambda a: (-a["count"], a["email"])),
            "free_slots": free_slots(events, day_start, day_end, tz),
        },
        "events": event_dicts,
    }


def free_slots(events: list[Event], day_start: datetime, day_end: datetime, tz: ZoneInfo) -> list[dict[str, Any]]:
    """Gaps longer than FREE_SLOT_MIN_MINUTES between merged busy blocks."""
    # Same-zone datetime subtraction is wall-clock in Python; UTC keeps DST days at 23 or 25 hours.
    day_start, day_end = day_start.astimezone(UTC), day_end.astimezone(UTC)
    blocks: list[tuple[datetime, datetime]] = []
    for e in sorted(events, key=lambda e: e.start_time):
        if not _blocks_time(e):
            continue
        start, end = max(e.start_time, day_start), min(e.end_time, day_end)
        if start >= end:
            continue
        if blocks and start <= blocks[-1][1]:
            blocks[-1] = (blocks[-1][0], max(blocks[-1][1], end))
        else:
            blocks.append((start, end))

    slots: list[dict[str, Any]] = []
    cursor = day_start
    for start, end in [*blocks, (day_end, day_end)]:
        if (start - cursor).total_seconds() / 60 > FREE_SLOT_MIN_MINUTES:
            slots.append(
                {
                    "start": cursor.astimezone(tz).isoformat(),
                    "end": start.astimezone(tz).isoformat(),
                    "duration_minutes": int((start - cursor).total_seconds() // 60),
                }
            )
        cursor = max(cursor, end)
    return slots
