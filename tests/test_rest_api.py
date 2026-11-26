"""Contract tests for the REST API: paths, status codes and response shapes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from kairos.app import create_app
from kairos.service import CalendarService

from .conftest import AUTH, TOKEN, FakeSource, fast_breaker, future, make_settings, unavailable

ALARM_EVENT = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Apple Inc.//iPhone OS 18//EN
BEGIN:VTIMEZONE
TZID:America/New_York
BEGIN:STANDARD
DTSTART:20071104T020000
RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU
TZOFFSETFROM:-0400
TZOFFSETTO:-0500
TZNAME:EST
END:STANDARD
BEGIN:DAYLIGHT
DTSTART:20070311T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU
TZOFFSETFROM:-0500
TZOFFSETTO:-0400
TZNAME:EDT
END:DAYLIGHT
END:VTIMEZONE
BEGIN:VEVENT
UID:alarm-uid
DTSTAMP:20260101T000000Z
SUMMARY:Dentist
DTSTART;TZID=America/New_York:{start}
DTEND;TZID=America/New_York:{end}
CATEGORIES:Health
ORGANIZER;CN=Office:mailto:office@example.com
ATTENDEE;CN=Eric;PARTSTAT=ACCEPTED:mailto:eric@example.com
X-APPLE-TRAVEL-ADVISORY-BEHAVIOR:AUTOMATIC
SEQUENCE:3
BEGIN:VALARM
ACTION:DISPLAY
TRIGGER:-PT30M
DESCRIPTION:Reminder
END:VALARM
END:VEVENT
END:VCALENDAR
"""


def _seed_alarm_event(home: FakeSource) -> str:
    local = (
        (datetime.now(UTC) + timedelta(days=5))
        .astimezone(ZoneInfo("America/New_York"))
        .replace(hour=10, minute=0, second=0, microsecond=0)
    )
    fmt = "%Y%m%dT%H%M%S"
    text = ALARM_EVENT.format(start=local.strftime(fmt), end=(local + timedelta(hours=1)).strftime(fmt))
    return home.put_raw(text.replace("\n", "\r\n"))


def _create(client: TestClient, **body: object) -> dict:
    start = future()
    payload = {"title": "Lunch", "start_time": start.isoformat(), "end_time": (start + timedelta(hours=1)).isoformat()}
    payload.update(body)
    resp = client.post("/api/v1/calendars/home/events", json=payload, headers=AUTH)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ----------------------------------------------------------------------------- auth


class TestAuth:
    def test_api_requires_a_token(self, client: TestClient) -> None:
        resp = client.get("/api/v1/calendars")
        assert resp.status_code == 401
        assert resp.headers["www-authenticate"].startswith("Bearer")

    def test_wrong_token_rejected(self, client: TestClient) -> None:
        assert client.get("/api/v1/calendars", headers={"Authorization": "Bearer nope"}).status_code == 401

    def test_valid_token_accepted(self, client: TestClient) -> None:
        assert client.get("/api/v1/calendars", headers=AUTH).status_code == 200

    def test_health_and_metrics_are_public(self, client: TestClient) -> None:
        assert client.get("/health").status_code == 200
        assert client.get("/metrics").status_code == 200

    def test_no_tokens_configured_refuses_everything(self) -> None:
        settings = make_settings(api_tokens=frozenset())
        service = CalendarService(settings, FakeSource, breaker_factory=fast_breaker)
        with TestClient(create_app(settings, service)) as c:
            assert c.get("/api/v1/calendars", headers=AUTH).status_code == 401

    def test_insecure_mode_skips_auth(self) -> None:
        settings = make_settings(api_tokens=frozenset(), insecure_no_auth=True)
        service = CalendarService(settings, FakeSource, breaker_factory=fast_breaker)
        with TestClient(create_app(settings, service)) as c:
            assert c.get("/api/v1/calendars").status_code == 200


# ----------------------------------------------------------------------------- reads


class TestReads:
    def test_list_calendars_reports_config(self, client: TestClient) -> None:
        body = client.get("/api/v1/calendars", headers=AUTH).json()
        assert body == {
            "calendars": [
                {"id": "home", "name": "Home", "writable": True, "timezone": "America/New_York"},
                {"id": "work", "name": "Work", "writable": False, "timezone": "UTC"},
            ]
        }

    def test_unknown_calendar_404(self, client: TestClient) -> None:
        resp = client.get("/api/v1/calendars/nope/events", headers=AUTH)
        assert resp.status_code == 404
        assert resp.json()["error"] == "calendar_not_found"

    def test_list_events_shape_and_limit(self, client: TestClient) -> None:
        for i in range(3):
            _create(
                client,
                title=f"E{i}",
                start_time=future(days=1 + i).isoformat(),
                end_time=future(days=1 + i, hours=1).isoformat(),
            )
        body = client.get("/api/v1/calendars/home/events?limit=2", headers=AUTH).json()
        assert body["calendar_id"] == "home"
        assert body["calendar_name"] == "Home"
        assert body["total_count"] == 2
        assert body["matched_count"] == 3
        assert body["truncated"] is True
        event = body["events"][0]
        for key in (
            "uid",
            "title",
            "description",
            "start_time",
            "end_time",
            "all_day",
            "attendees",
            "location",
            "recurrence_rule",
            "last_modified",
            "status",
        ):
            assert key in event
        assert event["title"] == "E0"

    def test_list_events_expands_recurring(self, client: TestClient) -> None:
        _create(client, title="Standup", recurrence_rule="FREQ=DAILY;COUNT=5")
        body = client.get("/api/v1/calendars/home/events", headers=AUTH).json()
        standups = [e for e in body["events"] if e["title"] == "Standup"]
        assert len(standups) == 5
        assert all(e["recurring"] for e in standups)

    def test_bad_date_range(self, client: TestClient) -> None:
        resp = client.get("/api/v1/calendars/home/events?date_min=2026-09-01&date_max=2026-08-01", headers=AUTH)
        assert resp.status_code == 400
        resp = client.get("/api/v1/calendars/home/events?date_min=garbage", headers=AUTH)
        assert resp.status_code == 400

    def test_range_capped(self, client: TestClient) -> None:
        resp = client.get("/api/v1/calendars/home/events?date_min=2026-01-01&date_max=2028-01-01", headers=AUTH)
        assert resp.status_code == 400

    def test_get_single_event(self, client: TestClient) -> None:
        uid = _create(client)["uid"]
        resp = client.get(f"/api/v1/calendars/home/events/{uid}", headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["uid"] == uid
        assert client.get("/api/v1/calendars/home/events/missing", headers=AUTH).status_code == 404

    def test_source_down_is_503(self, client: TestClient, home: FakeSource) -> None:
        home.fail_with = unavailable()
        resp = client.get("/api/v1/calendars/home/events", headers=AUTH)
        assert resp.status_code == 503
        assert resp.json()["error"] == "source_unavailable"


# ----------------------------------------------------------------------------- writes


class TestCreate:
    def test_create_returns_event(self, client: TestClient, home: FakeSource) -> None:
        body = _create(client, location="Cafe", attendees=[{"email": "a@example.com", "name": "A"}])
        assert body["uid"] in home.store
        assert body["link"] == f"/api/v1/calendars/home/events/{body['uid']}"
        assert body["location"] == "Cafe"
        assert body["attendees"] == [{"email": "a@example.com", "name": "A", "status": "needs-action"}]

    def test_create_stores_calendar_timezone(self, client: TestClient, home: FakeSource) -> None:
        uid = _create(client)["uid"]
        assert "DTSTART;TZID=America/New_York:" in home.store[uid]
        assert "BEGIN:VTIMEZONE" in home.store[uid]

    def test_create_requires_offset(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/calendars/home/events",
            headers=AUTH,
            json={"title": "x", "start_time": "2030-01-01T09:00:00", "end_time": "2030-01-01T10:00:00"},
        )
        assert resp.status_code == 400

    def test_create_end_before_start(self, client: TestClient) -> None:
        start = future()
        resp = client.post(
            "/api/v1/calendars/home/events",
            headers=AUTH,
            json={"title": "x", "start_time": start.isoformat(), "end_time": (start - timedelta(hours=1)).isoformat()},
        )
        assert resp.status_code == 400

    def test_create_bad_rrule(self, client: TestClient) -> None:
        start = future()
        resp = client.post(
            "/api/v1/calendars/home/events",
            headers=AUTH,
            json={
                "title": "x",
                "start_time": start.isoformat(),
                "end_time": (start + timedelta(hours=1)).isoformat(),
                "recurrence_rule": "FREQ=BOGUS",
            },
        )
        assert resp.status_code == 400

    def test_create_bad_status_rejected(self, client: TestClient) -> None:
        start = future()
        resp = client.post(
            "/api/v1/calendars/home/events",
            headers=AUTH,
            json={
                "title": "x",
                "start_time": start.isoformat(),
                "end_time": (start + timedelta(hours=1)).isoformat(),
                "status": "maybe",
            },
        )
        assert resp.status_code == 422

    def test_read_only_calendar_refuses_writes(self, client: TestClient) -> None:
        start = future()
        resp = client.post(
            "/api/v1/calendars/work/events",
            headers=AUTH,
            json={"title": "x", "start_time": start.isoformat(), "end_time": (start + timedelta(hours=1)).isoformat()},
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == "calendar_read_only"
        assert client.delete("/api/v1/calendars/work/events/any", headers=AUTH).status_code == 403


class TestUpdate:
    def test_partial_update_keeps_everything_else(self, client: TestClient, home: FakeSource) -> None:
        uid = _seed_alarm_event(home)
        resp = client.put(f"/api/v1/calendars/home/events/{uid}", json={"title": "Dentist (moved)"}, headers=AUTH)
        assert resp.status_code == 200, resp.text
        stored = home.store[uid]
        assert "SUMMARY:Dentist (moved)" in stored
        assert "BEGIN:VALARM" in stored and "TRIGGER:-PT30M" in stored
        assert "CATEGORIES:Health" in stored
        assert "ORGANIZER;CN=Office:mailto:office@example.com" in stored
        assert "PARTSTAT=ACCEPTED" in stored
        assert "X-APPLE-TRAVEL-ADVISORY-BEHAVIOR:AUTOMATIC" in stored
        assert "DTSTART;TZID=America/New_York:" in stored

    def test_patch_verb_also_works(self, client: TestClient) -> None:
        uid = _create(client)["uid"]
        resp = client.patch(f"/api/v1/calendars/home/events/{uid}", json={"location": "Office"}, headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["location"] == "Office"

    def test_reschedule_keeps_attendee_rsvp(self, client: TestClient, home: FakeSource) -> None:
        uid = _seed_alarm_event(home)
        new_start = future(days=6)
        resp = client.put(
            f"/api/v1/calendars/home/events/{uid}",
            headers=AUTH,
            json={
                "start_time": new_start.isoformat(),
                "attendees": [{"email": "eric@example.com", "name": "Eric"}, {"email": "new@example.com"}],
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["start_time"] == new_start.isoformat()
        # moving only the start keeps the one-hour length
        assert datetime.fromisoformat(body["end_time"]) - new_start == timedelta(hours=1)
        statuses = {a["email"]: a["status"] for a in body["attendees"]}
        assert statuses == {"eric@example.com": "accepted", "new@example.com": "needs-action"}

    def test_clear_location_with_empty_string(self, client: TestClient) -> None:
        uid = _create(client, location="Cafe")["uid"]
        resp = client.put(f"/api/v1/calendars/home/events/{uid}", json={"location": ""}, headers=AUTH)
        assert resp.json()["location"] is None

    def test_recurrence_rule_cannot_be_patched(self, client: TestClient) -> None:
        uid = _create(client)["uid"]
        resp = client.put(f"/api/v1/calendars/home/events/{uid}", json={"recurrence_rule": "FREQ=DAILY"}, headers=AUTH)
        assert resp.status_code == 422

    def test_recurring_title_change_keeps_rrule(self, client: TestClient, home: FakeSource) -> None:
        uid = _create(client, title="Standup", recurrence_rule="FREQ=DAILY")["uid"]
        resp = client.put(f"/api/v1/calendars/home/events/{uid}", json={"title": "Daily sync"}, headers=AUTH)
        assert resp.status_code == 200
        assert "RRULE:FREQ=DAILY" in home.store[uid]

    def test_recurring_time_change_rejected(self, client: TestClient) -> None:
        uid = _create(client, recurrence_rule="FREQ=DAILY")["uid"]
        resp = client.put(
            f"/api/v1/calendars/home/events/{uid}", headers=AUTH, json={"start_time": future(days=3).isoformat()}
        )
        assert resp.status_code == 409

    def test_recurring_series_started_in_past_can_be_renamed(self, client: TestClient, home: FakeSource) -> None:
        """1.x locked any series whose first occurrence was in the past."""
        past = datetime.now(UTC) - timedelta(days=30)
        uid = home.put_raw(
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:old-series\r\nSUMMARY:Standup\r\n"
            f"DTSTART:{past:%Y%m%dT%H%M%SZ}\r\nDTEND:{past + timedelta(minutes=15):%Y%m%dT%H%M%SZ}\r\n"
            "RRULE:FREQ=DAILY\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        resp = client.put(f"/api/v1/calendars/home/events/{uid}", json={"title": "Sync"}, headers=AUTH)
        assert resp.status_code == 200

    def test_locked_within_window(self, client: TestClient) -> None:
        start = datetime.now(UTC) + timedelta(minutes=5)
        uid = _create(client, start_time=start.isoformat(), end_time=(start + timedelta(hours=1)).isoformat())["uid"]
        resp = client.put(f"/api/v1/calendars/home/events/{uid}", json={"title": "X"}, headers=AUTH)
        assert resp.status_code == 409
        assert resp.json()["error"] == "event_locked"

    def test_empty_update_rejected(self, client: TestClient) -> None:
        uid = _create(client)["uid"]
        assert client.put(f"/api/v1/calendars/home/events/{uid}", json={}, headers=AUTH).status_code == 400

    def test_update_missing_event(self, client: TestClient) -> None:
        resp = client.put("/api/v1/calendars/home/events/missing", json={"title": "x"}, headers=AUTH)
        assert resp.status_code == 404
        assert resp.json()["error"] == "event_not_found"

    def test_end_before_start_after_merge(self, client: TestClient) -> None:
        uid = _create(client)["uid"]
        resp = client.put(
            f"/api/v1/calendars/home/events/{uid}", headers=AUTH, json={"end_time": future(days=1).isoformat()}
        )
        assert resp.status_code == 400

    def test_make_all_day(self, client: TestClient) -> None:
        uid = _create(client)["uid"]
        resp = client.put(f"/api/v1/calendars/home/events/{uid}", json={"all_day": True}, headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["all_day"] is True


class TestDelete:
    def test_delete(self, client: TestClient, home: FakeSource) -> None:
        uid = _create(client)["uid"]
        resp = client.delete(f"/api/v1/calendars/home/events/{uid}", headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"
        assert uid not in home.store

    def test_delete_past_event_locked(self, client: TestClient, home: FakeSource) -> None:
        past = datetime.now(UTC) - timedelta(hours=2)
        uid = home.put_raw(
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:past\r\nSUMMARY:Past\r\n"
            f"DTSTART:{past:%Y%m%dT%H%M%SZ}\r\nDTEND:{past + timedelta(hours=1):%Y%m%dT%H%M%SZ}\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        assert client.delete(f"/api/v1/calendars/home/events/{uid}", headers=AUTH).status_code == 409

    def test_writes_are_not_retried(self, client: TestClient, home: FakeSource) -> None:
        """A timed-out PUT may still have landed; retrying could create duplicates."""
        uid = _create(client)["uid"]
        home.calls.clear()
        original = home.delete_event

        def flaky(u: str) -> None:
            home.calls.append("delete_event")
            raise unavailable()

        home.delete_event = flaky  # type: ignore[method-assign]
        assert client.delete(f"/api/v1/calendars/home/events/{uid}", headers=AUTH).status_code == 503
        assert home.calls.count("delete_event") == 1
        home.delete_event = original  # type: ignore[method-assign]


# ----------------------------------------------------------------------------- brief + health


class TestBriefAndHealth:
    def test_today_shape(self, client: TestClient) -> None:
        resp = client.get("/api/v1/calendars/home/today?timezone=America/New_York", headers=AUTH)
        assert resp.status_code == 200
        body = resp.json()
        assert body["timezone"] == "America/New_York"
        for key in (
            "total_events",
            "total_calendar_time_minutes",
            "first_event_start",
            "last_event_end",
            "top_attendees",
            "free_slots",
        ):
            assert key in body["summary"]

    def test_today_defaults_to_calendar_zone(self, client: TestClient) -> None:
        assert client.get("/api/v1/calendars/home/today", headers=AUTH).json()["timezone"] == "America/New_York"

    def test_today_bad_timezone(self, client: TestClient) -> None:
        assert client.get("/api/v1/calendars/home/today?timezone=Mars/Base", headers=AUTH).status_code == 400

    def test_health_reports_sources(self, client: TestClient) -> None:
        body = client.get("/health").json()
        assert body["status"] == "healthy"
        assert body["calendars"]["home"]["status"] == "connected"
        assert body["calendars"]["home"]["circuit_breaker"]["state"] == "closed"

    def test_health_503_when_nothing_connects(self) -> None:
        settings = make_settings()

        class Down(FakeSource):
            def connect(self) -> None:
                raise unavailable()

        service = CalendarService(settings, Down, breaker_factory=fast_breaker)
        with TestClient(create_app(settings, service)) as c:
            resp = c.get("/health")
            assert resp.status_code == 503
            assert resp.json()["calendars"]["home"]["status"] in {"disconnected", "degraded"}

    def test_metrics_use_route_templates(self, client: TestClient) -> None:
        uid = _create(client)["uid"]
        client.get(f"/api/v1/calendars/home/events/{uid}", headers=AUTH)
        text = client.get("/metrics").text
        assert 'path="/api/v1/calendars/{calendar_id}/events/{uid}"' in text
        assert uid not in text


def test_token_constant() -> None:
    assert len(TOKEN) >= 32
