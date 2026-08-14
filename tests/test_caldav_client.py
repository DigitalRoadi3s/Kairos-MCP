"""
Test scaffolding for CalDAV iCalendar parsing (task 9, Haiku tier).

Fixtures and harness only. Assertions for the genuinely tricky edge
cases (DST transitions, malformed VTIMEZONE, floating time) are left as
TODOs for the Sonnet coverage pass (task 22) — filling those in requires
judgment calls this scaffold shouldn't guess at.
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from caldav_client import (  # noqa: E402
    CalDAVAuthError,
    CalDAVNotFoundError,
    CalDAVSourceClient,
    CalDAVUnavailableError,
)


@pytest.fixture
def timed_event_ics():
    return """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:test-uid-1
SUMMARY:Team Standup
DTSTART;TZID=America/New_York:20260812T090000
DTEND;TZID=America/New_York:20260812T093000
LOCATION:Room 1
ATTENDEE;CN=Alice;PARTSTAT=ACCEPTED:mailto:alice@company.com
ATTENDEE;CN=Bob;PARTSTAT=TENTATIVE:mailto:bob@company.com
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR"""


@pytest.fixture
def all_day_event_ics():
    return """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:test-uid-2
SUMMARY:Company Holiday
DTSTART;VALUE=DATE:20260812
DTEND;VALUE=DATE:20260813
END:VEVENT
END:VCALENDAR"""


@pytest.fixture
def floating_time_event_ics():
    """No TZID, no Z suffix — ambiguous local time with no zone info."""
    return """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:test-uid-3
SUMMARY:Floating Event
DTSTART:20260812T090000
DTEND:20260812T093000
END:VEVENT
END:VCALENDAR"""


class TestParseIcal:
    def test_timed_event_converts_to_utc(self, timed_event_ics):
        event = CalDAVSourceClient._parse_ical(timed_event_ics)
        assert event.uid == "test-uid-1"
        assert event.title == "Team Standup"
        assert not event.all_day
        assert event.start_time.tzinfo is not None

    def test_all_day_event_has_no_tz_dependency(self, all_day_event_ics):
        event = CalDAVSourceClient._parse_ical(all_day_event_ics)
        assert event.all_day
        assert event.start_time.hour == 0

    def test_attendee_parsing(self, timed_event_ics):
        event = CalDAVSourceClient._parse_ical(timed_event_ics)
        assert len(event.attendees) == 2
        assert event.attendees[0].email == "alice@company.com"
        assert event.attendees[0].status == "accepted"

    def test_floating_time_event(self, floating_time_event_ics):
        """No TZID and no Z suffix: icalendar returns a naive datetime,
        which we treat as UTC (best-effort) and flag via original_timezone
        being None so downstream code knows it's a guess, not a fact."""
        event = CalDAVSourceClient._parse_ical(floating_time_event_ics)
        assert event.start_time.tzinfo is not None  # we always produce aware datetimes
        assert event.start_time.hour == 9  # treated as if it were already UTC
        assert event.original_timezone is None  # flags this as a guess

    def test_dst_transition(self):
        """Event spanning the America/New_York spring-forward boundary
        (2026-03-08, clocks jump 02:00 -> 03:00). A 2-hour wall-clock
        span should compute as a 1-hour real duration."""
        ics = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:dst-test
SUMMARY:Spans DST
DTSTART;TZID=America/New_York:20260308T013000
DTEND;TZID=America/New_York:20260308T033000
END:VEVENT
END:VCALENDAR"""
        event = CalDAVSourceClient._parse_ical(ics)
        assert event.start_time.isoformat() == "2026-03-08T06:30:00+00:00"
        assert event.end_time.isoformat() == "2026-03-08T07:30:00+00:00"
        assert (event.end_time - event.start_time).total_seconds() == 3600

    def test_malformed_vtimezone(self):
        """A TZID with no matching VTIMEZONE block (or a corrupted one)
        does not raise - icalendar itself degrades to a naive datetime,
        which our parser then treats the same as floating time."""
        ics = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:malformed-tz
SUMMARY:Bad Timezone Ref
DTSTART;TZID=Nonexistent/Zone:20260812T090000
DTEND;TZID=Nonexistent/Zone:20260812T093000
END:VEVENT
END:VCALENDAR"""
        event = CalDAVSourceClient._parse_ical(ics)  # must not raise
        assert event.uid == "malformed-tz"
        assert event.original_timezone is None


class TestConnectErrors:
    """
    Verifies connect() maps every caldav/requests exception to the
    correct CalDAVError subclass per the mapping table in
    caldav_client.py's connect() docstring.
    """

    def _client(self):
        return CalDAVSourceClient("icloud", "https://caldav.icloud.com/", "u", "p")

    def test_auth_error_maps_to_caldav_auth_error(self):
        from caldav.lib.error import AuthorizationError
        client = self._client()
        with patch("caldav_client.caldav.DAVClient") as MockDAV:
            MockDAV.return_value.principal.side_effect = AuthorizationError("bad creds")
            with pytest.raises(CalDAVAuthError):
                client.connect()

    def test_not_found_error_maps_to_caldav_not_found_error(self):
        from caldav.lib.error import NotFoundError
        client = self._client()
        with patch("caldav_client.caldav.DAVClient") as MockDAV:
            MockDAV.return_value.calendar.side_effect = NotFoundError("no such calendar")
            client.calendar_path = "/some/path/"
            with pytest.raises(CalDAVNotFoundError):
                client.connect()

    def test_ssl_error_maps_to_caldav_unavailable_error(self):
        from requests.exceptions import SSLError
        client = self._client()
        with patch("caldav_client.caldav.DAVClient") as MockDAV:
            MockDAV.return_value.principal.side_effect = SSLError("cert verify failed")
            with pytest.raises(CalDAVUnavailableError):
                client.connect()

    def test_timeout_maps_to_caldav_unavailable_error(self):
        from requests.exceptions import Timeout
        client = self._client()
        with patch("caldav_client.caldav.DAVClient") as MockDAV:
            MockDAV.return_value.principal.side_effect = Timeout("timed out")
            with pytest.raises(CalDAVUnavailableError):
                client.connect()

    def test_rate_limit_error_maps_to_caldav_unavailable_error(self):
        """iCloud returns HTTP 403 for rate limiting (not the more common
        429), which python-caldav 2.x+ surfaces as RateLimitError. Must
        map to a clean 503, not propagate unhandled. Imports from
        caldav_client (not caldav.lib.error directly) since older caldav
        versions (the pinned 1.3.9) don't define this class at all -
        caldav_client.py provides a fallback so this import always works."""
        from caldav_client import RateLimitError
        client = self._client()
        with patch("caldav_client.caldav.DAVClient") as MockDAV:
            MockDAV.return_value.principal.side_effect = RateLimitError("403 rate limited")
            with pytest.raises(CalDAVUnavailableError):
                client.connect()
