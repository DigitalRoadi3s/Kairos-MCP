"""Daily brief maths: busy time, free slots, caching."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from kairos.brief import BriefCache, compute_brief, day_bounds
from kairos.models import Attendee, Event

NY = ZoneInfo("America/New_York")


def ev(uid: str, start: datetime, minutes: int, **kw: object) -> Event:
    return Event(uid=uid, title=uid, start_time=start, end_time=start + timedelta(minutes=minutes), **kw)  # type: ignore[arg-type]


def brief(events: list[Event], day: datetime = datetime(2026, 8, 12, 12, tzinfo=UTC)) -> dict:
    start, end = day_bounds(NY, day)
    return compute_brief(events, calendar_id="home", calendar_name="Home", tz=NY, day_start=start, day_end=end)


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 12, hour, minute, tzinfo=NY).astimezone(UTC)


def test_day_bounds_dst() -> None:
    start, end = day_bounds(NY, datetime(2026, 3, 8, 15, tzinfo=UTC))
    assert end.astimezone(UTC) - start.astimezone(UTC) == timedelta(hours=23)
    slots = brief([], datetime(2026, 3, 8, 15, tzinfo=UTC))["summary"]["free_slots"]
    assert slots[0]["duration_minutes"] == 23 * 60


def test_empty_day() -> None:
    b = brief([])
    assert b["summary"]["total_events"] == 0
    assert b["summary"]["free_slots"] == [
        {
            "start": at(0).astimezone(NY).isoformat(),
            "end": datetime(2026, 8, 13, tzinfo=NY).isoformat(),
            "duration_minutes": 1440,
        }
    ]


def test_busy_time_skips_all_day_cancelled_and_free() -> None:
    events = [
        ev("a", at(9), 60),
        ev("b", at(10), 30, status="cancelled"),
        ev("c", at(11), 30, transparent=True),
        Event(uid="d", title="Holiday", start_time=at(0), end_time=at(0) + timedelta(days=1), all_day=True),
    ]
    s = brief(events)["summary"]
    assert s["total_events"] == 4
    assert s["total_calendar_time_minutes"] == 60


def test_overnight_event_clipped_to_today() -> None:
    s = brief([ev("late", at(23), 120)])["summary"]
    assert s["total_calendar_time_minutes"] == 60


def test_free_slots_merge_overlaps() -> None:
    s = brief([ev("a", at(9), 60), ev("b", at(9, 30), 60), ev("c", at(13), 30)])["summary"]
    starts = [slot["start"] for slot in s["free_slots"]]
    assert at(10, 30).astimezone(NY).isoformat() in starts
    assert at(9, 30).astimezone(NY).isoformat() not in starts


def test_top_attendees() -> None:
    events = [
        ev("a", at(9), 30, attendees=[Attendee("x@e.com", "X"), Attendee("y@e.com")]),
        ev("b", at(10), 30, attendees=[Attendee("X@e.com", "X")]),
    ]
    top = brief(events)["summary"]["top_attendees"]
    assert top[0]["count"] == 2 and top[0]["email"].lower() == "x@e.com"


def test_times_in_requested_zone() -> None:
    b = brief([ev("a", at(9), 30)])
    assert b["events"][0]["start_time"].startswith("2026-08-12T09:00:00-04:00")
    assert b["summary"]["first_event_start"] == b["events"][0]["start_time"]


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_cache_ttl_and_stale() -> None:
    clock = Clock()
    cache = BriefCache(ttl=300, clock=clock)
    key = ("home", "America/New_York", date(2026, 8, 12))
    cache.set(key, {"x": 1})
    assert cache.get(key) == {"x": 1}
    clock.now = 301
    assert cache.get(key) is None
    assert cache.get(key, allow_stale=True) == {"x": 1}


D = date(2026, 8, 12)


def test_cache_invalidate() -> None:
    cache = BriefCache()
    cache.set(("home", "UTC", D), {"x": 1})
    cache.set(("work", "UTC", D), {"x": 2})
    cache.invalidate("home")
    assert cache.get(("home", "UTC", D)) is None
    assert cache.get(("work", "UTC", D)) == {"x": 2}
