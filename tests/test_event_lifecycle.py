"""
Integration test scaffolding: create -> read -> update -> delete lifecycle
(task 15, Haiku tier). Fixtures and harness only.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import main  # noqa: E402
from caldav_client import CalDAVNotFoundError, Event  # noqa: E402


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture
def fake_caldav_client():
    fake = MagicMock()
    main.app.state.clients = {"icloud": fake}
    yield fake
    main.app.state.clients = {}


@pytest.fixture
def future_window():
    start = datetime.now(timezone.utc) + timedelta(days=2)
    return start, start + timedelta(hours=1)


class TestEventLifecycle:
    """
    TODO(sonnet, task 22): flesh out into a real end-to-end sequence where
    each step's mock is driven off the PREVIOUS step's actual response
    (not independently stubbed per-call as in the smoke test this
    scaffold started from) - e.g. capture the ical string passed to
    create_event and feed it back through update_event/delete_event to
    catch any drift between what we write and what we later look up.
    """

    def test_create_then_read(self, client, fake_caldav_client, future_window):
        start, end = future_window
        fake_caldav_client.create_event.side_effect = lambda ical: Event(
            uid="lifecycle-uid", title="Lifecycle Test", start_time=start, end_time=end
        )
        resp = client.post("/api/v1/calendars/icloud/events", json={
            "title": "Lifecycle Test",
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
        })
        assert resp.status_code == 201
        assert resp.json()["uid"] == "lifecycle-uid"

    def test_update_preserves_unset_fields(self, client, fake_caldav_client, future_window):
        """PATCH semantics: PUT with only {"title": ...} must not touch
        location/description/attendees. Verified by capturing the ical
        actually passed to update_event and confirming it still carries
        the existing event's location - not blanked out."""
        start, end = future_window
        existing = Event(uid="preserve-uid", title="Original", start_time=start, end_time=end,
                          location="Original Room", description="Original desc")
        fake_caldav_client.get_event.return_value = existing

        captured = {}

        def capture_update(uid, ical):
            captured["ical"] = ical
            return Event(uid=uid, title="Renamed", start_time=start, end_time=end,
                         location="Original Room", description="Original desc")

        fake_caldav_client.update_event.side_effect = capture_update

        resp = client.put("/api/v1/calendars/icloud/events/preserve-uid", json={"title": "Renamed"})
        assert resp.status_code == 200
        assert resp.json()["title"] == "Renamed"
        # the ical sent to CalDAV must still contain the untouched fields
        assert "Original Room" in captured["ical"]
        assert "Original desc" in captured["ical"]

    def test_update_recurring_event_time_rejected(self, client, fake_caldav_client, future_window):
        start, end = future_window
        fake_caldav_client.get_event.return_value = Event(
            uid="recurring-uid", title="Standup", start_time=start, end_time=end,
            recurrence_rule="FREQ=DAILY",
        )
        resp = client.put("/api/v1/calendars/icloud/events/recurring-uid",
                           json={"start_time": (start + timedelta(hours=1)).isoformat()})
        assert resp.status_code == 409

    def test_update_recurring_event_title_still_allowed(self, client, fake_caldav_client, future_window):
        """Sanity check for the test above: recurring events are only
        locked against TIME changes, not all changes."""
        start, end = future_window
        existing = Event(uid="recurring-uid2", title="Standup", start_time=start, end_time=end,
                          recurrence_rule="FREQ=DAILY")
        fake_caldav_client.get_event.return_value = existing
        fake_caldav_client.update_event.side_effect = lambda uid, ical: Event(
            uid=uid, title="Renamed Standup", start_time=start, end_time=end,
            recurrence_rule="FREQ=DAILY")
        resp = client.put("/api/v1/calendars/icloud/events/recurring-uid2",
                           json={"title": "Renamed Standup"})
        assert resp.status_code == 200

    def test_cache_invalidated_after_write(self, client, fake_caldav_client, future_window):
        """Confirms daily_brief.invalidate() is actually wired into the
        create/update/delete handlers (task 14), not just present
        somewhere in the codebase - by patching it and asserting it's
        called with the right calendar_id after each successful write."""
        import main as main_module
        start, end = future_window

        with patch.object(main_module.daily_brief, "invalidate") as mock_invalidate:
            fake_caldav_client.create_event.side_effect = lambda ical: Event(
                uid="cache-uid", title="T", start_time=start, end_time=end)
            resp = client.post("/api/v1/calendars/icloud/events", json={
                "title": "T", "start_time": start.isoformat(), "end_time": end.isoformat(),
            })
            assert resp.status_code == 201
            mock_invalidate.assert_called_once_with("icloud")

        with patch.object(main_module.daily_brief, "invalidate") as mock_invalidate:
            fake_caldav_client.get_event.return_value = Event(
                uid="cache-uid", title="T", start_time=start, end_time=end)
            fake_caldav_client.update_event.side_effect = lambda uid, ical: Event(
                uid=uid, title="T2", start_time=start, end_time=end)
            resp = client.put("/api/v1/calendars/icloud/events/cache-uid", json={"title": "T2"})
            assert resp.status_code == 200
            mock_invalidate.assert_called_once_with("icloud")

        with patch.object(main_module.daily_brief, "invalidate") as mock_invalidate:
            fake_caldav_client.get_event.return_value = Event(
                uid="cache-uid", title="T2", start_time=start, end_time=end)
            resp = client.delete("/api/v1/calendars/icloud/events/cache-uid")
            assert resp.status_code == 200
            mock_invalidate.assert_called_once_with("icloud")

    def test_update_locked_within_15_minutes(self, client, fake_caldav_client):
        soon = datetime.now(timezone.utc) + timedelta(minutes=5)
        fake_caldav_client.get_event.return_value = Event(
            uid="soon-uid", title="Soon", start_time=soon, end_time=soon + timedelta(hours=1)
        )
        resp = client.put("/api/v1/calendars/icloud/events/soon-uid", json={"title": "X"})
        assert resp.status_code == 409

    def test_delete_past_event_locked(self, client, fake_caldav_client):
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        fake_caldav_client.get_event.return_value = Event(
            uid="past-uid", title="Past", start_time=past, end_time=past + timedelta(hours=1)
        )
        resp = client.delete("/api/v1/calendars/icloud/events/past-uid")
        assert resp.status_code == 409

    def test_delete_future_event_succeeds(self, client, fake_caldav_client, future_window):
        start, end = future_window
        fake_caldav_client.get_event.return_value = Event(
            uid="future-uid", title="Future", start_time=start, end_time=end
        )
        resp = client.delete("/api/v1/calendars/icloud/events/future-uid")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    def test_update_nonexistent_event_404(self, client, fake_caldav_client):
        fake_caldav_client.get_event.side_effect = CalDAVNotFoundError("not found")
        resp = client.put("/api/v1/calendars/icloud/events/missing", json={"title": "X"})
        assert resp.status_code == 404
