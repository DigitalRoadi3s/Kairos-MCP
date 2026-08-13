"""
CalDAV client + iCalendar transform core (task 3, Opus tier).

This module defines the interfaces that Sonnet-tier tasks (4, 6, 11, 12, 13)
build against. Do not change the public shapes below without updating the
subagent plan — other tasks depend on them.

Design decisions (see PRD "iCloud CalDAV Gotchas" and NFR-2):
- All times are normalized to UTC internally. VTIMEZONE blocks are parsed
  for their offset and then discarded; the original zone name is kept in
  Event.original_timezone metadata only (Gotcha #6 — do not trust VTIMEZONE
  parsing across Linux libc/tzdata versions for anything but offset lookup).
- Connection pooling: one requests.Session per calendar source, pool size
  10 (NFR-2), reused across calls — do not construct a new DAVClient per
  request.
- Recurring events are expanded server-side within the requested date range
  via the CalDAV REPORT query, not expanded manually in Python — iCloud
  supports expand=true in the time-range REPORT. If a request doesn't
  specify a range, do NOT expand (Out of Scope: full recurring manipulation
  is read-only; unranged expansion is unbounded and must be rejected).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import caldav
from caldav.lib.error import AuthorizationError, NotFoundError
from icalendar import Calendar as ICalendar
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import SSLError, Timeout

logger = logging.getLogger("caldav_gateway.client")


# ---------------------------------------------------------------------------
# Public data shapes — Sonnet tasks (4, 6, 11-13) consume these directly.
# ---------------------------------------------------------------------------

@dataclass
class Attendee:
    email: str
    name: Optional[str] = None
    status: str = "needs-action"  # accepted | tentative | declined | needs-action


@dataclass
class Event:
    uid: str
    title: str
    start_time: datetime  # always tz-aware UTC
    end_time: datetime  # always tz-aware UTC
    description: Optional[str] = None
    all_day: bool = False
    location: Optional[str] = None
    attendees: list[Attendee] = field(default_factory=list)
    recurrence_rule: Optional[str] = None
    last_modified: Optional[datetime] = None
    status: str = "confirmed"
    original_timezone: Optional[str] = None  # metadata only, not for math


class CalDAVError(Exception):
    """Base class for all client errors. Routers map these to HTTP codes."""


class CalDAVAuthError(CalDAVError):
    """Maps to 401."""


class CalDAVNotFoundError(CalDAVError):
    """Maps to 404."""


class CalDAVUnavailableError(CalDAVError):
    """Maps to 503. Raised on timeout, connection error, SSL error."""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class CalDAVSourceClient:
    """
    Wraps one configured calendar source (one CalDAV URL + credentials).
    One instance per source, held for the process lifetime by config.py's
    startup validation (task 2) and reused by every request.
    """

    def __init__(self, source_id: str, url: str, username: str, password: str,
                 calendar_path: Optional[str] = None, timeout: int = 30):
        self.source_id = source_id
        self.url = url
        self.username = username
        self._password = password  # never log this
        self.calendar_path = calendar_path
        self.timeout = timeout
        self._client: Optional[caldav.DAVClient] = None
        self._calendar: Optional[caldav.Calendar] = None

    def connect(self) -> None:
        """
        Establish the DAVClient and resolve the target calendar. Called once
        at startup by config.py (task 2) to validate connectivity, and again
        transparently on reconnect after a circuit-breaker trip (task 16/17).

        Raises CalDAVAuthError / CalDAVUnavailableError / CalDAVNotFoundError.
        Never logs self._password.
        """
        try:
            self._client = caldav.DAVClient(
                url=self.url,
                username=self.username,
                password=self._password,
                timeout=self.timeout,
            )
            principal = self._client.principal()
            if self.calendar_path:
                self._calendar = self._client.calendar(url=self.calendar_path)
            else:
                calendars = principal.calendars()
                if not calendars:
                    raise CalDAVNotFoundError(
                        f"No calendars found for source '{self.source_id}'"
                    )
                self._calendar = calendars[0]
            # Touch the calendar to confirm the path actually resolves.
            self._calendar.get_properties()
        except AuthorizationError as exc:
            logger.error("caldav_auth_failed", extra={"source_id": self.source_id})
            raise CalDAVAuthError(
                f"Auth failed for source '{self.source_id}'. iCloud requires "
                "an app-specific password, not the account password — see "
                "PRD Appendix A Step 1."
            ) from exc
        except NotFoundError as exc:
            raise CalDAVNotFoundError(
                f"Calendar path not found for source '{self.source_id}': "
                f"{self.calendar_path}"
            ) from exc
        except SSLError as exc:
            raise CalDAVUnavailableError(
                f"TLS verification failed for '{self.source_id}' — check "
                "ca-certificates in the image (PRD Gotcha #3)."
            ) from exc
        except (Timeout, RequestsConnectionError) as exc:
            raise CalDAVUnavailableError(
                f"Could not reach CalDAV server for '{self.source_id}'."
            ) from exc

    def list_events(self, date_min: datetime, date_max: datetime) -> list[Event]:
        """
        FR-2 backing call. date_min/date_max MUST be tz-aware; caller
        (Sonnet task 4) is responsible for defaulting/validating range
        before calling this. Always bounded — never call without a range
        (see module docstring on recurring-event expansion).
        """
        self._require_connected()
        try:
            raw_events = self._calendar.date_search(
                start=date_min, end=date_max, expand=True
            )
        except (Timeout, RequestsConnectionError) as exc:
            raise CalDAVUnavailableError(str(exc)) from exc
        return [self._parse_ical(e.data) for e in raw_events]

    def get_event(self, uid: str) -> Event:
        self._require_connected()
        try:
            raw = self._calendar.event_by_uid(uid)
        except NotFoundError as exc:
            raise CalDAVNotFoundError(f"Event '{uid}' not found") from exc
        return self._parse_ical(raw.data)

    def _require_connected(self) -> None:
        if self._calendar is None:
            raise CalDAVUnavailableError(
                f"Source '{self.source_id}' not connected — call connect() first"
            )

    # -- iCalendar -> Event transform -------------------------------------

    @staticmethod
    def _parse_ical(ical_data: str) -> Event:
        """
        Parse a single VEVENT into our normalized Event shape.

        Timezone handling (Gotcha #6): VTIMEZONE is used ONLY to resolve
        each DATE-TIME's UTC offset at parse time via icalendar's built-in
        pytz-aware handling. Once resolved, we discard the tzinfo object
        and store aware UTC datetimes plus the *name* of the original zone
        as metadata — nothing downstream should re-derive offsets from
        original_timezone.
        """
        cal = ICalendar.from_ical(ical_data)
        vevent = next(c for c in cal.walk() if c.name == "VEVENT")

        dtstart = vevent.decoded("dtstart")
        dtend = vevent.decoded("dtend") if "dtend" in vevent else dtstart

        all_day = not isinstance(dtstart, datetime)
        original_tz = None

        if all_day:
            start_utc = datetime(dtstart.year, dtstart.month, dtstart.day,
                                  tzinfo=timezone.utc)
            end_utc = datetime(dtend.year, dtend.month, dtend.day,
                                tzinfo=timezone.utc)
        else:
            if dtstart.tzinfo is not None:
                original_tz = str(dtstart.tzinfo)
                start_utc = dtstart.astimezone(timezone.utc)
            else:
                # Floating time with no VTIMEZONE — treat as UTC and flag
                # via original_timezone=None so callers know it's a guess.
                start_utc = dtstart.replace(tzinfo=timezone.utc)
            if isinstance(dtend, datetime):
                end_utc = (dtend.astimezone(timezone.utc)
                           if dtend.tzinfo else dtend.replace(tzinfo=timezone.utc))
            else:
                end_utc = start_utc

        attendees = []
        for a in vevent.get("attendee", []):
            email = str(a).replace("mailto:", "").strip()
            params = getattr(a, "params", {})
            attendees.append(Attendee(
                email=email,
                name=params.get("CN"),
                status=str(params.get("PARTSTAT", "needs-action")).lower(),
            ))

        last_modified = None
        if "last-modified" in vevent:
            lm = vevent.decoded("last-modified")
            last_modified = (lm.astimezone(timezone.utc) if lm.tzinfo
                              else lm.replace(tzinfo=timezone.utc))

        return Event(
            uid=str(vevent.get("uid")),
            title=str(vevent.get("summary", "")),
            description=str(vevent.get("description", "")) or None,
            start_time=start_utc,
            end_time=end_utc,
            all_day=all_day,
            location=str(vevent.get("location", "")) or None,
            attendees=attendees,
            recurrence_rule=str(vevent.get("rrule", "")) or None,
            last_modified=last_modified,
            status=str(vevent.get("status", "confirmed")).lower(),
            original_timezone=original_tz,
        )
