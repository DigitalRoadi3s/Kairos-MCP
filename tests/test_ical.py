"""iCalendar parsing, generation and in-place patching."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from icalendar import Calendar

from kairos import ical
from kairos.errors import InvalidRequestError
from kairos.models import AttendeeIn, EventCreate, EventPatch

NY = ZoneInfo("America/New_York")


def vcal(*lines: str) -> str:
    return "\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0", "BEGIN:VEVENT", *lines, "END:VEVENT", "END:VCALENDAR", ""])


TIMED = vcal(
    "UID:test-uid-1",
    "SUMMARY:Team Standup",
    "DTSTART;TZID=America/New_York:20260812T090000",
    "DTEND;TZID=America/New_York:20260812T093000",
    "LOCATION:Room 1",
    "ATTENDEE;CN=Alice;PARTSTAT=ACCEPTED:mailto:alice@company.com",
    "ATTENDEE;CN=Bob;PARTSTAT=TENTATIVE:mailto:bob@company.com",
    "STATUS:CONFIRMED",
)


class TestParse:
    def test_timed_event_converts_to_utc(self) -> None:
        e = ical.parse_ical(TIMED)
        assert e.uid == "test-uid-1"
        assert e.title == "Team Standup"
        assert not e.all_day
        assert e.start_time == datetime(2026, 8, 12, 13, 0, tzinfo=UTC)
        assert e.end_time == datetime(2026, 8, 12, 13, 30, tzinfo=UTC)
        assert e.original_timezone == "America/New_York"
        assert e.location == "Room 1"
        assert e.status == "confirmed"

    def test_attendees(self) -> None:
        e = ical.parse_ical(TIMED)
        assert [(a.email, a.name, a.status) for a in e.attendees] == [
            ("alice@company.com", "Alice", "accepted"),
            ("bob@company.com", "Bob", "tentative"),
        ]

    def test_single_attendee(self) -> None:
        e = ical.parse_ical(
            vcal("UID:x", "DTSTART:20260812T090000Z", "DTEND:20260812T100000Z", "ATTENDEE:MAILTO:solo@example.com")
        )
        assert [a.email for a in e.attendees] == ["solo@example.com"]
        assert e.attendees[0].status == "needs-action"

    def test_all_day(self) -> None:
        e = ical.parse_ical(
            vcal("UID:x", "SUMMARY:Holiday", "DTSTART;VALUE=DATE:20260812", "DTEND;VALUE=DATE:20260813")
        )
        assert e.all_day
        assert e.start_time == datetime(2026, 8, 12, tzinfo=UTC)
        assert e.end_time == datetime(2026, 8, 13, tzinfo=UTC)

    def test_all_day_without_end_is_one_day(self) -> None:
        e = ical.parse_ical(vcal("UID:x", "DTSTART;VALUE=DATE:20260812"))
        assert e.end_time - e.start_time == timedelta(days=1)

    def test_timed_without_end_is_instant(self) -> None:
        e = ical.parse_ical(vcal("UID:x", "DTSTART:20260812T090000Z"))
        assert e.end_time == e.start_time

    def test_duration(self) -> None:
        e = ical.parse_ical(vcal("UID:x", "DTSTART:20260812T090000Z", "DURATION:PT45M"))
        assert e.end_time == datetime(2026, 8, 12, 9, 45, tzinfo=UTC)

    def test_floating_time_uses_source_zone(self) -> None:
        e = ical.parse_ical(vcal("UID:x", "DTSTART:20260812T090000", "DTEND:20260812T093000"), NY)
        assert e.start_time == datetime(2026, 8, 12, 13, 0, tzinfo=UTC)
        assert e.original_timezone is None

    def test_floating_time_defaults_to_utc(self) -> None:
        e = ical.parse_ical(vcal("UID:x", "DTSTART:20260812T090000", "DTEND:20260812T093000"))
        assert e.start_time.hour == 9

    def test_dst_transition(self) -> None:
        """01:30 to 03:30 local across spring-forward is one real hour."""
        e = ical.parse_ical(
            vcal(
                "UID:dst",
                "DTSTART;TZID=America/New_York:20260308T013000",
                "DTEND;TZID=America/New_York:20260308T033000",
            )
        )
        assert e.start_time.isoformat() == "2026-03-08T06:30:00+00:00"
        assert e.end_time.isoformat() == "2026-03-08T07:30:00+00:00"

    def test_unknown_tzid_does_not_raise(self) -> None:
        e = ical.parse_ical(
            vcal(
                "UID:bad-tz",
                "DTSTART;TZID=Nonexistent/Zone:20260812T090000",
                "DTEND;TZID=Nonexistent/Zone:20260812T093000",
            )
        )
        assert e.uid == "bad-tz"
        assert e.start_time.tzinfo is not None

    def test_transparent_and_cancelled(self) -> None:
        e = ical.parse_ical(
            vcal(
                "UID:x", "DTSTART:20260812T090000Z", "DTEND:20260812T100000Z", "TRANSP:TRANSPARENT", "STATUS:CANCELLED"
            )
        )
        assert e.transparent
        assert e.status == "cancelled"

    def test_master_preferred_over_override(self) -> None:
        text = "\r\n".join(
            [
                "BEGIN:VCALENDAR",
                "VERSION:2.0",
                "BEGIN:VEVENT",
                "UID:s",
                "SUMMARY:Moved one",
                "RECURRENCE-ID:20260813T090000Z",
                "DTSTART:20260813T100000Z",
                "DTEND:20260813T110000Z",
                "END:VEVENT",
                "BEGIN:VEVENT",
                "UID:s",
                "SUMMARY:Series",
                "RRULE:FREQ=DAILY",
                "DTSTART:20260812T090000Z",
                "DTEND:20260812T100000Z",
                "END:VEVENT",
                "END:VCALENDAR",
                "",
            ]
        )
        e = ical.parse_ical(text)
        assert e.title == "Series"
        assert e.recurrence_rule == "FREQ=DAILY"

    def test_garbage_rejected(self) -> None:
        with pytest.raises(InvalidRequestError):
            ical.parse_ical("not ical")


def _body(**kw: object) -> EventCreate:
    base: dict[str, object] = {
        "title": "Weekly Standup",
        "start_time": datetime(2026, 8, 17, 13, 0, tzinfo=UTC),
        "end_time": datetime(2026, 8, 17, 13, 30, tzinfo=UTC),
    }
    base.update(kw)
    return EventCreate.model_validate(base)


class TestBuild:
    def test_rrule_round_trips(self) -> None:
        _, text = ical.build_ical(_body(recurrence_rule="FREQ=WEEKLY;BYDAY=MO,WE,FR"), "UTC")
        assert ical.parse_ical(text).recurrence_rule == "FREQ=WEEKLY;BYDAY=MO,WE,FR"

    @pytest.mark.parametrize("rule", ["FREQ=BOGUS", "garbage", "BYDAY=MO", ""])
    def test_invalid_rrule(self, rule: str) -> None:
        if rule == "":
            return  # empty means "none"
        with pytest.raises(InvalidRequestError):
            ical.build_ical(_body(recurrence_rule=rule), "UTC")

    def test_writes_tzid_and_vtimezone(self) -> None:
        uid, text = ical.build_ical(_body(), "America/New_York")
        assert "DTSTART;TZID=America/New_York:20260817T090000" in text
        assert "BEGIN:VTIMEZONE" in text and "TZID:America/New_York" in text
        e = ical.parse_ical(text)
        assert e.uid == uid
        assert e.start_time == datetime(2026, 8, 17, 13, 0, tzinfo=UTC)
        assert e.original_timezone == "America/New_York"

    def test_timezone_field_overrides_default(self) -> None:
        _, text = ical.build_ical(_body(timezone="Europe/Madrid"), "America/New_York")
        assert "DTSTART;TZID=Europe/Madrid:20260817T150000" in text

    def test_utc_written_as_z(self) -> None:
        _, text = ical.build_ical(_body(), "UTC")
        assert "DTSTART:20260817T130000Z" in text
        assert "VTIMEZONE" not in text

    def test_required_properties(self) -> None:
        _, text = ical.build_ical(_body(), "UTC")
        for prop in ("PRODID:", "DTSTAMP:", "CREATED:", "LAST-MODIFIED:", "SEQUENCE:0", "STATUS:CONFIRMED"):
            assert prop in text

    def test_all_day_uses_dates_in_zone(self) -> None:
        body = _body(
            all_day=True,
            start_time=datetime(2026, 8, 17, 2, 0, tzinfo=UTC),
            end_time=datetime(2026, 8, 17, 3, 0, tzinfo=UTC),
        )
        _, text = ical.build_ical(body, "America/New_York")
        # 02:00 UTC is still the 16th in New York; a same-day range becomes one day.
        assert "DTSTART;VALUE=DATE:20260816" in text
        assert "DTEND;VALUE=DATE:20260817" in text

    def test_attendees_need_action(self) -> None:
        _, text = ical.build_ical(_body(attendees=[AttendeeIn(email="a@example.com", name="A")]), "UTC")
        e = ical.parse_ical(text)
        assert e.attendees[0].email == "a@example.com"
        assert e.attendees[0].status == "needs-action"

    def test_naive_times_rejected(self) -> None:
        with pytest.raises(InvalidRequestError):
            ical.build_ical(_body(start_time=datetime(2026, 8, 17, 9), end_time=datetime(2026, 8, 17, 10)), "UTC")

    def test_end_before_start(self) -> None:
        with pytest.raises(InvalidRequestError):
            ical.build_ical(_body(end_time=datetime(2026, 8, 17, 12, 0, tzinfo=UTC)), "UTC")

    def test_bad_timezone(self) -> None:
        with pytest.raises(InvalidRequestError):
            ical.build_ical(_body(timezone="Mars/Base"), "UTC")


RICH = vcal(
    "UID:rich",
    "DTSTAMP:20260101T000000Z",
    "SUMMARY:Dentist",
    "DTSTART;TZID=America/New_York:20260812T090000",
    "DURATION:PT1H",
    "CATEGORIES:Health",
    "ATTENDEE;CN=Eric;PARTSTAT=ACCEPTED;ROLE=CHAIR:mailto:eric@example.com",
    "X-CUSTOM:keep me",
    "BEGIN:VALARM",
    "ACTION:DISPLAY",
    "TRIGGER:-PT30M",
    "DESCRIPTION:Reminder",
    "END:VALARM",
)


def _patch(text: str, **kw: object) -> tuple[str, object]:
    cal = Calendar.from_ical(text)
    vevent = ical.apply_patch(cal, EventPatch.model_validate(kw), "UTC")
    return cal.to_ical().decode(), ical.parse_component(vevent)


class TestPatch:
    def test_title_only_preserves_the_rest(self) -> None:
        out, _ = _patch(RICH, title="Dentist (moved)")
        assert "SUMMARY:Dentist (moved)" in out
        for kept in (
            "BEGIN:VALARM",
            "TRIGGER:-PT30M",
            "CATEGORIES:Health",
            "X-CUSTOM:keep me",
            "PARTSTAT=ACCEPTED",
            "ROLE=CHAIR",
            "DURATION:PT1H",
            "TZID=America/New_York",
        ):
            assert kept in out

    def test_move_start_keeps_length_and_zone(self) -> None:
        new = datetime(2026, 8, 20, 18, 0, tzinfo=UTC)
        out, e = _patch(RICH, start_time=new)
        assert e.start_time == new  # type: ignore[attr-defined]
        assert e.end_time == new + timedelta(hours=1)  # type: ignore[attr-defined]
        assert "DTSTART;TZID=America/New_York:20260820T140000" in out
        assert "DURATION" not in out and "DTEND;TZID=America/New_York:20260820T150000" in out

    def test_end_before_start(self) -> None:
        with pytest.raises(InvalidRequestError):
            _patch(RICH, end_time=datetime(2026, 8, 1, tzinfo=UTC))

    def test_attendee_rsvp_kept(self) -> None:
        _, e = _patch(RICH, attendees=[{"email": "ERIC@example.com"}, {"email": "new@example.com", "name": "N"}])
        assert [(a.email, a.status) for a in e.attendees] == [  # type: ignore[attr-defined]
            ("ERIC@example.com", "accepted"),
            ("new@example.com", "needs-action"),
        ]

    def test_clear_description(self) -> None:
        out, e = _patch(RICH, description="")
        assert e.description is None  # type: ignore[attr-defined]
        assert "BEGIN:VALARM" in out  # the alarm's own DESCRIPTION is untouched

    def test_status(self) -> None:
        _, e = _patch(RICH, status="tentative")
        assert e.status == "tentative"  # type: ignore[attr-defined]

    def test_to_all_day_and_back(self) -> None:
        out, e = _patch(RICH, all_day=True)
        assert e.all_day  # type: ignore[attr-defined]
        assert "DTSTART;VALUE=DATE:20260812" in out
        with pytest.raises(InvalidRequestError):
            _patch(out, all_day=False)
        _, timed = _patch(
            out,
            all_day=False,
            start_time=datetime(2026, 8, 12, 13, tzinfo=UTC),
            end_time=datetime(2026, 8, 12, 14, tzinfo=UTC),
        )
        assert not timed.all_day  # type: ignore[attr-defined]

    def test_last_modified_bumped(self) -> None:
        out, _ = _patch(RICH, title="x")
        assert "LAST-MODIFIED:" in out
        assert "DTSTAMP:20260101T000000Z" not in out


def test_date_helpers() -> None:
    assert ical.parse_ical(vcal("UID:x", "DTSTART;VALUE=DATE:20261231")).start_time.date() == date(2026, 12, 31)
