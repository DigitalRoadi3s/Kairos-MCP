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
try:
    from caldav.lib.error import RateLimitError
except ImportError:
    # caldav < 2.x doesn't define this class. Fall back to a class that
    # never actually gets raised by the library on this version - the
    # except clauses below become inert rather than crashing at import
    # time. A 403 rate-limit response on old caldav still surfaces as
    # whatever generic error the library raises for it (usually a bare
    # DAVError), which the module doesn't specifically catch and thus
    # ends up an unhandled 500 - a known gap on caldav<2.x, tracked but
    # not blocking since the pinned requirements.txt version is 1.3.9.
    class RateLimitError(Exception):
        pass
from icalendar import Calendar as ICalendar
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import SSLError, Timeout

logger = logging.getLogger("caldav_gateway.client")


@dataclass
class Attendee:
    email: str
    name: Optional[str] = None
    status: str = "needs-action"


@dataclass
class Event:
    uid: str
    title: str
    start_time: datetime
    end_time: datetime
    description: Optional[str] = None
    all_day: bool = False
    location: Optional[str] = None
    attendees: list[Attendee] = field(default_factory=list)
    recurrence_rule: Optional[str] = None
    last_modified: Optional[datetime] = None
    status: str = "confirmed"
    original_timezone: Optional[str] = None


class CalDAVError(Exception):
    """Base class for all client errors. Routers map these to HTTP codes."""


class CalDAVAuthError(CalDAVError):
    """Maps to 401."""


class CalDAVNotFoundError(CalDAVError):
    """Maps to 404."""


class CalDAVUnavailableError(CalDAVError):
    """Maps to 503. Raised on timeout, connection error, SSL error, rate limit."""


class CalDAVSourceClient:
    """
    Wraps one configured calendar source (one CalDAV URL + credentials).
    One instance per source, held for the process lifetime by config.py's
    startup validation (task 2) and reused by every request.
    """

    def __init__(self, source_id: str, url: str, username: str = None, password: str = None,
                 calendar_path: Optional[str] = None, timeout: int = 30, auth=None):
        """
        auth: an optional requests/niquests-compatible AuthBase instance
        (e.g. google_oauth.GoogleOAuth2Auth). When provided, it's used
        INSTEAD of username/password - see connect() below. Kept as a
        separate param rather than overloading username/password so the
        two auth modes can't be silently mixed up by a config typo.
        """
        self.source_id = source_id
        self.url = url
        self.username = username
        self._password = password
        self._auth = auth
        self.calendar_path = calendar_path
        self.timeout = timeout
        self._client: Optional[caldav.DAVClient] = None
        self._calendar: Optional[caldav.Calendar] = None

    def connect(self) -> None:
        try:
            if self._auth is not None:
                self._client = caldav.DAVClient(
                    url=self.url,
                    auth=self._auth,
                    timeout=self.timeout,
                )
            else:
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
            self._calendar.get_properties()
        except AuthorizationError as exc:
            logger.error("caldav_auth_failed", extra={"source_id": self.source_id})
            if self._auth is not None:
                hint = ("OAuth2 token was rejected - the refresh token may be "
                        "invalid, expired, or missing the calendar scope.")
            else:
                hint = ("iCloud requires an app-specific password, not the "
                        "account password.")
            raise CalDAVAuthError(
                f"Auth failed for source '{self.source_id}'. {hint}"
            ) from exc
        except NotFoundError as exc:
            raise CalDAVNotFoundError(
                f"Calendar path not found for source '{self.source_id}': "
                f"{self.calendar_path}"
            ) from exc
        except RateLimitError as exc:
            raise CalDAVUnavailableError(
                f"Rate limited by CalDAV server for '{self.source_id}' "
                "(iCloud returns 403 for this, not 429 - PRD Gotcha #4)."
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
        self._require_connected()
        try:
            raw_events = self._calendar.date_search(
                start=date_min, end=date_max, expand=True
            )
        except RateLimitError as exc:
            raise CalDAVUnavailableError(f"Rate limited: {exc}") from exc
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

    def create_event(self, ical_data: str) -> Event:
        self._require_connected()
        try:
            self._calendar.save_event(ical_data)
        except RateLimitError as exc:
            raise CalDAVUnavailableError(f"Rate limited: {exc}") from exc
        except (Timeout, RequestsConnectionError) as exc:
            raise CalDAVUnavailableError(str(exc)) from exc
        return self._parse_ical(ical_data)

    def update_event(self, uid: str, ical_data: str) -> Event:
        self._require_connected()
        try:
            existing = self._calendar.event_by_uid(uid)
        except NotFoundError as exc:
            raise CalDAVNotFoundError(f"Event '{uid}' not found") from exc
        try:
            existing.data = ical_data
            existing.save()
        except RateLimitError as exc:
            raise CalDAVUnavailableError(f"Rate limited: {exc}") from exc
        except (Timeout, RequestsConnectionError) as exc:
            raise CalDAVUnavailableError(str(exc)) from exc
        return self._parse_ical(ical_data)

    def delete_event(self, uid: str) -> None:
        self._require_connected()
        try:
            existing = self._calendar.event_by_uid(uid)
        except NotFoundError as exc:
            raise CalDAVNotFoundError(f"Event '{uid}' not found") from exc
        try:
            existing.delete()
        except RateLimitError as exc:
            raise CalDAVUnavailableError(f"Rate limited: {exc}") from exc
        except (Timeout, RequestsConnectionError) as exc:
            raise CalDAVUnavailableError(str(exc)) from exc

    def _require_connected(self) -> None:
        if self._calendar is None:
            raise CalDAVUnavailableError(
                f"Source '{self.source_id}' not connected — call connect() first"
            )

    @staticmethod
    def _parse_ical(ical_data: str) -> Event:
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
                start_utc = dtstart.replace(tzinfo=timezone.utc)
            if isinstance(dtend, datetime):
                end_utc = (dtend.astimezone(timezone.utc)
                           if dtend.tzinfo else dtend.replace(tzinfo=timezone.utc))
            else:
                end_utc = start_utc

        attendees = []
        raw_attendees = vevent.get("attendee", [])
        if not isinstance(raw_attendees, list):
            raw_attendees = [raw_attendees]
        for a in raw_attendees:
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

        rrule_prop = vevent.get("rrule")
        recurrence_rule = rrule_prop.to_ical().decode("utf-8") if rrule_prop else None

        return Event(
            uid=str(vevent.get("uid")),
            title=str(vevent.get("summary", "")),
            description=str(vevent.get("description", "")) or None,
            start_time=start_utc,
            end_time=end_utc,
            all_day=all_day,
            location=str(vevent.get("location", "")) or None,
            attendees=attendees,
            recurrence_rule=recurrence_rule,
            last_modified=last_modified,
            status=str(vevent.get("status", "confirmed")).lower(),
            original_timezone=original_tz,
        )
