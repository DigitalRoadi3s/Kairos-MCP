"""
Integration test scaffolding: create -> read -> update -> delete lifecycle
(task 15, Haiku tier). Fixtures and harness only.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

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

    @pytest.mark.skip(reason="TODO(sonnet, task 22): chain create's returned "
                              "uid into update, assert the merged fields "
                              "(PATCH semantics - unset fields keep old values)")
    def test_update_preserves_unset_fields(self, client, fake_caldav_client, future_window):
        pass

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

    @pytest.mark.skip(reason="TODO(sonnet, task 22): recurring-event "
                              "immutability - update with start_time/end_time "
                              "set on an event that has recurrence_rule "
                              "should 409, per FR-4 constraint")
    def test_update_recurring_event_time_rejected(self, client, fake_caldav_client):
        pass

    @pytest.mark.skip(reason="TODO(sonnet, task 22): concurrent create+read "
                              "against the daily_brief cache - confirm "
                              "invalidate() ordering holds under real "
                              "concurrency, not just the single-threaded "
                              "unit test in daily_brief's own test file")
    def test_cache_invalidated_after_write(self, client, fake_caldav_client):
        pass
