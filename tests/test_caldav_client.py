"""
Test scaffolding for CalDAV iCalendar parsing (task 9, Haiku tier).

Fixtures and harness only. Assertions for the genuinely tricky edge
cases (DST transitions, malformed VTIMEZONE, floating time) are left as
TODOs for the Sonnet coverage pass (task 22) — filling those in requires
judgment calls this scaffold shouldn't guess at.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from caldav_client import CalDAVSourceClient  # noqa: E402


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

    @pytest.mark.skip(reason="TODO(sonnet, task 22): decide + assert the "
                              "intended fallback behavior for floating time "
                              "(currently treated as UTC in caldav_client.py "
                              "— confirm that's correct for iCloud's actual "
                              "output before asserting on it)")
    def test_floating_time_event(self, floating_time_event_ics):
        pass

    @pytest.mark.skip(reason="TODO(sonnet, task 22): DST transition case — "
                              "event spanning a spring-forward/fall-back "
                              "boundary in America/New_York")
    def test_dst_transition(self):
        pass

    @pytest.mark.skip(reason="TODO(sonnet, task 22): malformed/missing "
                              "VTIMEZONE block — should degrade gracefully, "
                              "not raise")
    def test_malformed_vtimezone(self):
        pass


class TestConnectErrors:
    """
    TODO(sonnet, task 22): fill in with mocked caldav.DAVClient raising
    AuthorizationError / NotFoundError / SSLError / Timeout, asserting
    each maps to the correct CalDAVError subclass (see caldav_client.py's
    connect() for the mapping table).
    """
    pass
