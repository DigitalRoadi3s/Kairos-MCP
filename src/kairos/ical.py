"""iCalendar parsing, generation and in-place editing.

Reads normalize every time to tz-aware UTC. Writes keep the event's own
time zone (TZID plus a generated VTIMEZONE) so phones and desktop clients
show it in the zone it was booked in.

Updates edit the stored VEVENT in place instead of rebuilding it, so
anything Kairos doesn't model (alarms, categories, organizer, EXDATEs,
X- properties, attendee RSVPs) survives an edit.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from icalendar import Calendar, vCalAddress, vRecur, vText
from icalendar import Event as IEvent

from kairos.errors import InvalidRequestError
from kairos.models import Attendee, AttendeeIn, Event, EventCreate, EventPatch

PRODID = "-//Kairos-MCP//EN"


# --------------------------------------------------------------------------- reading


def _as_utc(value: datetime, floating_tz: ZoneInfo) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=floating_tz).astimezone(UTC)
    return value.astimezone(UTC)


def _date_start_utc(value: date, floating_tz: ZoneInfo) -> datetime:
    """Midnight of an all-day date. Kept as UTC midnight so the date itself never shifts."""
    del floating_tz
    return datetime(value.year, value.month, value.day, tzinfo=UTC)


def _zone_name(value: datetime) -> str | None:
    tz = value.tzinfo
    if isinstance(tz, ZoneInfo):
        return tz.key
    if tz is not None and value.utcoffset() == timedelta(0):
        return "UTC"
    return None


def master_component(cal: Calendar) -> Any:
    """The series master (no RECURRENCE-ID), or the only VEVENT."""
    events = [c for c in cal.walk("VEVENT")]
    if not events:
        raise InvalidRequestError("Calendar object contains no VEVENT")
    for component in events:
        if "RECURRENCE-ID" not in component:
            return component
    return events[0]


def parse_component(vevent: Any, floating_tz: ZoneInfo | None = None) -> Event:
    """Turn one VEVENT into an Event. Floating times are read in floating_tz (default UTC)."""
    ftz = floating_tz or ZoneInfo("UTC")
    dtstart = vevent.decoded("DTSTART")
    all_day = not isinstance(dtstart, datetime)

    if "DTEND" in vevent:
        dtend = vevent.decoded("DTEND")
    elif "DURATION" in vevent:
        dtend = dtstart + vevent.decoded("DURATION")
    else:
        # RFC 5545 3.6.1: a date-only event with no end lasts one day; a timed one is instantaneous.
        dtend = dtstart + timedelta(days=1) if all_day else dtstart

    if all_day:
        start_utc = _date_start_utc(dtstart, ftz)
        end_day = dtend if not isinstance(dtend, datetime) else dtend.date()
        end_utc = _date_start_utc(end_day, ftz)
        original_tz = None
    else:
        start_utc = _as_utc(dtstart, ftz)
        end_utc = _as_utc(dtend, ftz) if isinstance(dtend, datetime) else start_utc
        original_tz = _zone_name(dtstart)

    attendees: list[Attendee] = []
    raw = vevent.get("ATTENDEE", [])
    for addr in raw if isinstance(raw, list) else [raw]:
        params = getattr(addr, "params", {})
        email = str(addr)
        if email.lower().startswith("mailto:"):
            email = email[7:]
        attendees.append(
            Attendee(
                email=email.strip(),
                name=str(params["CN"]) if params.get("CN") else None,
                status=str(params.get("PARTSTAT", "NEEDS-ACTION")).lower(),
            )
        )

    last_modified = None
    if "LAST-MODIFIED" in vevent:
        last_modified = _as_utc(vevent.decoded("LAST-MODIFIED"), ZoneInfo("UTC"))

    recurrence_id = None
    if "RECURRENCE-ID" in vevent:
        rid = vevent.decoded("RECURRENCE-ID")
        recurrence_id = _as_utc(rid, ftz) if isinstance(rid, datetime) else _date_start_utc(rid, ftz)

    rrule = vevent.get("RRULE")
    description = str(vevent.get("DESCRIPTION", "")) or None
    location = str(vevent.get("LOCATION", "")) or None

    return Event(
        uid=str(vevent.get("UID", "")),
        title=str(vevent.get("SUMMARY", "")),
        description=description,
        start_time=start_utc,
        end_time=end_utc,
        all_day=all_day,
        location=location,
        attendees=attendees,
        recurrence_rule=rrule.to_ical().decode() if rrule else None,
        recurrence_id=recurrence_id,
        last_modified=last_modified,
        status=str(vevent.get("STATUS", "CONFIRMED")).lower(),
        transparent=str(vevent.get("TRANSP", "OPAQUE")).upper() == "TRANSPARENT",
        original_timezone=original_tz,
    )


def parse_ical(data: str, floating_tz: ZoneInfo | None = None) -> Event:
    """Parse a whole VCALENDAR and return its master event."""
    try:
        cal = Calendar.from_ical(data)
    except ValueError as exc:
        raise InvalidRequestError(f"Unparseable iCalendar data: {exc}") from exc
    return parse_component(master_component(cal), floating_tz)


# --------------------------------------------------------------------------- writing


def _validate_rrule(rule: str) -> vRecur:
    try:
        parsed = vRecur.from_ical(rule)
    except (ValueError, KeyError, TypeError) as exc:
        raise InvalidRequestError(f"Invalid recurrence_rule: {exc}") from exc
    if "FREQ" not in parsed:
        raise InvalidRequestError("Invalid recurrence_rule: FREQ is required")
    return parsed


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (KeyError, ValueError) as exc:
        raise InvalidRequestError(f"Unknown timezone {name!r}") from exc


def _require_aware(*values: datetime | None) -> None:
    for value in values:
        if value is not None and value.tzinfo is None:
            raise InvalidRequestError("Timestamps must include a timezone offset (e.g. 2026-08-20T09:00:00-04:00)")


def _set_times(vevent: Any, start: datetime | date, end: datetime | date) -> None:
    for key in ("DTSTART", "DTEND", "DURATION"):
        vevent.pop(key, None)
    vevent.add("DTSTART", start)
    vevent.add("DTEND", end)


def _all_day_dates(start: datetime | date, end: datetime | date) -> tuple[date, date]:
    s = start.date() if isinstance(start, datetime) else start
    e = end.date() if isinstance(end, datetime) else end
    if e <= s:
        e = s + timedelta(days=1)
    return s, e


def _timed(value: datetime, tz: ZoneInfo) -> datetime:
    if tz.key == "UTC":
        return value.astimezone(UTC)
    return value.astimezone(tz)


def _make_attendee(email: str, name: str | None, params: dict[str, Any] | None = None) -> vCalAddress:
    addr = vCalAddress(f"mailto:{email}")
    for key, value in (params or {}).items():
        addr.params[key] = value
    addr.params.setdefault("PARTSTAT", "NEEDS-ACTION")
    addr.params.setdefault("ROLE", "REQ-PARTICIPANT")
    if name:
        addr.params["CN"] = vText(name)
    return addr


def build_ical(body: EventCreate, default_tz: str, uid: str | None = None) -> tuple[str, str]:
    """Build a new VCALENDAR for a create request. Returns (uid, ical_text)."""
    _require_aware(body.start_time, body.end_time)
    tz = _zone(body.timezone or default_tz)
    if not body.all_day and body.start_time >= body.end_time:
        raise InvalidRequestError("start_time must be before end_time")

    cal = Calendar()
    cal.add("PRODID", PRODID)
    cal.add("VERSION", "2.0")
    event = IEvent()
    uid = uid or str(uuid.uuid4())
    now = datetime.now(UTC)
    event.add("UID", uid)
    event.add("DTSTAMP", now)
    event.add("CREATED", now)
    event.add("LAST-MODIFIED", now)
    event.add("SEQUENCE", 0)
    event.add("SUMMARY", body.title)
    if body.description:
        event.add("DESCRIPTION", body.description)
    if body.location:
        event.add("LOCATION", body.location)
    event.add("STATUS", body.status.upper())
    if body.recurrence_rule:
        event.add("RRULE", _validate_rrule(body.recurrence_rule))

    if body.all_day:
        _set_times(event, *_all_day_dates(body.start_time.astimezone(tz), body.end_time.astimezone(tz)))
    else:
        _set_times(event, _timed(body.start_time, tz), _timed(body.end_time, tz))

    for attendee in body.attendees:
        event.add("ATTENDEE", _make_attendee(attendee.email, attendee.name), encode=False)

    cal.add_component(event)
    cal.add_missing_timezones()
    return uid, cal.to_ical().decode()


def _existing_zone(value: Any, fallback: ZoneInfo) -> ZoneInfo:
    if isinstance(value, datetime):
        if isinstance(value.tzinfo, ZoneInfo):
            return value.tzinfo
        if value.tzinfo is not None:
            return ZoneInfo("UTC")
    return fallback


def _replace_attendees(vevent: Any, new: list[AttendeeIn]) -> None:
    """Swap in a new attendee list, keeping RSVP and other params for people who stay."""
    raw = vevent.get("ATTENDEE", [])
    existing: dict[str, dict[str, Any]] = {}
    for addr in raw if isinstance(raw, list) else [raw]:
        email = str(addr)
        email = email[7:] if email.lower().startswith("mailto:") else email
        existing[email.strip().lower()] = dict(getattr(addr, "params", {}))
    vevent.pop("ATTENDEE", None)
    for attendee in new:
        params = existing.get(attendee.email.strip().lower())
        vevent.add("ATTENDEE", _make_attendee(attendee.email, attendee.name, params), encode=False)


def _set_or_clear(vevent: Any, key: str, value: str | None) -> None:
    if value is None:
        return
    vevent.pop(key, None)
    if value != "":
        vevent.add(key, value)


def apply_patch(cal: Calendar, patch: EventPatch, default_tz: str) -> Any:
    """Edit the series master in place. Returns the edited VEVENT.

    Raises InvalidRequestError when the result would be invalid. Business rules
    (lock window, read-only calendars, recurring-time changes) live in the service.
    """
    _require_aware(patch.start_time, patch.end_time)
    vevent = master_component(cal)
    fallback = _zone(default_tz)

    if patch.title is not None:
        vevent.pop("SUMMARY", None)
        vevent.add("SUMMARY", patch.title)
    _set_or_clear(vevent, "DESCRIPTION", patch.description)
    _set_or_clear(vevent, "LOCATION", patch.location)
    if patch.status is not None:
        vevent.pop("STATUS", None)
        vevent.add("STATUS", patch.status.upper())
    if patch.attendees is not None:
        _replace_attendees(vevent, patch.attendees)

    touches_time = patch.start_time is not None or patch.end_time is not None or patch.all_day is not None
    if touches_time:
        old_start = vevent.decoded("DTSTART")
        if "DTEND" in vevent:
            old_end = vevent.decoded("DTEND")
        elif "DURATION" in vevent:
            old_end = old_start + vevent.decoded("DURATION")
        else:
            old_end = old_start + timedelta(days=1) if not isinstance(old_start, datetime) else old_start
        was_all_day = not isinstance(old_start, datetime)
        all_day = was_all_day if patch.all_day is None else patch.all_day
        tz = _existing_zone(old_start, fallback)

        def as_dt(value: datetime | date) -> datetime:
            if isinstance(value, datetime):
                return value if value.tzinfo else value.replace(tzinfo=fallback)
            return datetime(value.year, value.month, value.day, 9, tzinfo=tz)

        start = patch.start_time or as_dt(old_start)
        end = patch.end_time or as_dt(old_end)
        if patch.start_time is not None and patch.end_time is None and not was_all_day:
            # Moving only the start keeps the event's length, like a calendar app drag.
            end = start + (as_dt(old_end) - as_dt(old_start))
        if all_day:
            if was_all_day and patch.start_time is None and patch.end_time is None:
                pass  # all_day=True on an all-day event: nothing to change
            else:
                local_start = start.astimezone(tz)
                local_end = end.astimezone(tz)
                _set_times(vevent, *_all_day_dates(local_start, local_end))
        else:
            if was_all_day and patch.start_time is None:
                raise InvalidRequestError("Switching an all-day event to timed needs start_time and end_time")
            if start >= end:
                raise InvalidRequestError("start_time must be before end_time")
            _set_times(vevent, _timed(start, tz), _timed(end, tz))

    now = datetime.now(UTC)
    for key in ("DTSTAMP", "LAST-MODIFIED"):
        vevent.pop(key, None)
        vevent.add(key, now)
    cal.add_missing_timezones()
    return vevent
