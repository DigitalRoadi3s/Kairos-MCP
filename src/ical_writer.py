"""
iCalendar generation / write-back (task 10, Opus tier).

Builds valid VEVENT objects from request JSON and pushes them via CalDAV
PUT. Interface for Sonnet tasks 11-13: call create_event() / update_event()
/ delete_event() on CalDAVSourceClient (added to caldav_client.py below via
this module's functions - see the bottom of this file for the client
method additions).

Design decisions:
- all-day events use VALUE=DATE (no time component), timed events use
  VALUE=DATE-TIME in UTC (Z suffix) - we do NOT attach VTIMEZONE blocks
  on write, since generating correct VTIMEZONE data is its own can of
  worms (Gotcha #6) and iCloud accepts UTC DATE-TIME just fine. The
  PRD's optional "timezone" field on create is accepted for record-
  keeping (round-tripped in our own iCal transform's original_timezone
  metadata) but does NOT change what's written to the wire - everything
  written is UTC.
- UID generation: if the caller doesn't supply one (create only), we
  generate a UUID4. update/delete always require an existing UID.
- 15-minute lock window (FR-4/FR-5 "only future events... within 15 min
  of start time are locked") and the recurrence-immutable constraint are
  enforced by the Sonnet-tier endpoint handlers (tasks 12/13), NOT here -
  this module only knows how to build/write iCal, not the business rules
  about when a write is allowed. Keeps the two concerns separable.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

from icalendar import Calendar as ICalendar
from icalendar import Event as IEvent
from icalendar import vCalAddress, vRecur, vText


@dataclass
class EventInput:
    """What Sonnet's create/update endpoints (tasks 11, 12) parse the
    request body into before calling generate_ical()."""
    title: str
    start_time: datetime  # tz-aware
    end_time: datetime  # tz-aware
    description: Optional[str] = None
    all_day: bool = False
    location: Optional[str] = None
    attendees: list[tuple[str, Optional[str]]] = field(default_factory=list)  # (email, name)
    status: str = "confirmed"
    uid: Optional[str] = None  # None on create -> generated; required on update
    recurrence_rule: Optional[str] = None  # RFC 5545 RRULE value, e.g.
    # "FREQ=WEEKLY;BYDAY=MO,WE,FR" (task 25 - promotes recurring events
    # from read-only to full write support). Validated via icalendar's
    # own RRULE parser before being written - see generate_ical().


def generate_ical(data: EventInput) -> str:
    """
    Builds a complete VCALENDAR/VEVENT string ready for CalDAV PUT.
    Raises ValueError on malformed input (caller/task 11-12 should have
    already validated start<end etc. per FR-3, but this is a second
    line of defense - never emit invalid iCal).
    """
    if data.start_time >= data.end_time and not data.all_day:
        raise ValueError("start_time must be before end_time")

    cal = ICalendar()
    cal.add("prodid", "-//Kairos-MCP//EN")
    cal.add("version", "2.0")

    event = IEvent()
    uid = data.uid or str(uuid.uuid4())
    event.add("uid", uid)
    event.add("summary", data.title)
    if data.description:
        event.add("description", data.description)
    if data.location:
        event.add("location", data.location)
    event.add("status", data.status.upper())

    if data.recurrence_rule:
        try:
            rrule = vRecur.from_ical(data.recurrence_rule)
        except (ValueError, KeyError) as exc:
            raise ValueError(f"Invalid recurrence_rule: {exc}") from exc
        event.add("rrule", rrule)
    event.add("dtstamp", datetime.now(timezone.utc))

    if data.all_day:
        # VALUE=DATE per RFC 5545 - date objects only, no time/tz.
        event.add("dtstart", data.start_time.date())
        event.add("dtend", data.end_time.date() if data.end_time > data.start_time
                   else data.start_time.date())
    else:
        start_utc = data.start_time.astimezone(timezone.utc)
        end_utc = data.end_time.astimezone(timezone.utc)
        event.add("dtstart", start_utc)
        event.add("dtend", end_utc)

    for email, name in data.attendees:
        attendee = vCalAddress(f"mailto:{email}")
        if name:
            attendee.params["cn"] = vText(name)
        attendee.params["partstat"] = vText("NEEDS-ACTION")
        attendee.params["role"] = vText("REQ-PARTICIPANT")
        event.add("attendee", attendee, encode=0)

    cal.add_component(event)
    return cal.to_ical().decode("utf-8")
