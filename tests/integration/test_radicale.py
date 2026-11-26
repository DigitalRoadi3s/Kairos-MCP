"""CalDAVSource against a real CalDAV server (Radicale), started per module.

Run with: pytest -m integration
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from caldav.davclient import DAVClient

from kairos import ical
from kairos.errors import ConflictError, EventNotFoundError, UpstreamAuthError
from kairos.models import EventCreate, EventPatch
from kairos.settings import SourceConfig
from kairos.sources.caldav_source import CalDAVSource

pytestmark = pytest.mark.integration


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def radicale(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    root = tmp_path_factory.mktemp("radicale")
    users = root / "users"
    users.write_text("eric:secret\n")
    config = root / "config"
    port = _free_port()
    config.write_text(
        "[server]\n"
        f"hosts = 127.0.0.1:{port}\n"
        "[auth]\ntype = htpasswd\n"
        f"htpasswd_filename = {users}\nhtpasswd_encryption = plain\n"
        f"[storage]\nfilesystem_folder = {root / 'collections'}\n"
        "[logging]\nlevel = warning\n"
    )
    proc = subprocess.Popen([sys.executable, "-m", "radicale", "--config", str(config)])
    url = f"http://127.0.0.1:{port}/"
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("Radicale did not start")
    client = DAVClient(url=url, username="eric", password="secret")
    client.principal().make_calendar(name="Home", cal_id="home")
    yield url
    proc.terminate()
    proc.wait(timeout=10)


def cfg(url: str, **kw: object) -> SourceConfig:
    base: dict[str, object] = {
        "id": "home",
        "name": "Home",
        "url": url,
        "username": "eric",
        "password": "secret",
        "calendar_path": f"{url}eric/home/",
        "timezone": "America/New_York",
        "timeout": 10,
    }
    base.update(kw)
    return SourceConfig(**base)  # type: ignore[arg-type]


@pytest.fixture
def src(radicale: str) -> Iterator[CalDAVSource]:
    s = CalDAVSource(cfg(radicale))
    s.connect()
    yield s
    s.close()


def window() -> tuple[datetime, datetime]:
    now = datetime.now(UTC)
    return now - timedelta(days=1), now + timedelta(days=30)


def create(src: CalDAVSource, **kw: object) -> str:
    start = (datetime.now(UTC) + timedelta(days=3)).replace(microsecond=0)
    body = {"title": "Lunch", "start_time": start, "end_time": start + timedelta(hours=1)}
    body.update(kw)
    uid, text = ical.build_ical(EventCreate.model_validate(body), src.config.timezone)
    src.create_event(text)
    return uid


def test_bad_password(radicale: str) -> None:
    with pytest.raises(UpstreamAuthError):
        CalDAVSource(cfg(radicale, password="wrong")).connect()


def test_discovers_first_calendar_without_path(radicale: str) -> None:
    s = CalDAVSource(cfg(radicale, calendar_path=None))
    s.connect()
    assert s.connected


def test_create_get_list(src: CalDAVSource) -> None:
    uid = create(src, location="Cafe")
    event = src.get_event(uid)
    assert event.title == "Lunch"
    assert event.location == "Cafe"
    assert event.original_timezone == "America/New_York"
    assert uid in {e.uid for e in src.list_events(*window())}


def test_recurring_expanded(src: CalDAVSource) -> None:
    uid = create(src, title="Standup", recurrence_rule="FREQ=DAILY;COUNT=4")
    occurrences = [e for e in src.list_events(*window()) if e.uid == uid]
    assert len(occurrences) == 4
    assert all(e.recurrence_id is not None for e in occurrences)
    assert src.get_event(uid).recurrence_rule == "FREQ=DAILY;COUNT=4"


def test_update_preserves_alarm_and_rsvp(src: CalDAVSource, radicale: str) -> None:
    start = datetime.now(UTC) + timedelta(days=4)
    text = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\nBEGIN:VEVENT\r\nUID:rich-1\r\n"
        f"DTSTAMP:{start:%Y%m%dT%H%M%SZ}\r\nDTSTART:{start:%Y%m%dT%H%M%SZ}\r\n"
        f"DTEND:{start + timedelta(hours=1):%Y%m%dT%H%M%SZ}\r\nSUMMARY:Dentist\r\nCATEGORIES:Health\r\nSEQUENCE:2\r\n"
        "ATTENDEE;CN=Eric;PARTSTAT=ACCEPTED:mailto:eric@example.com\r\n"
        "BEGIN:VALARM\r\nACTION:DISPLAY\r\nTRIGGER:-PT30M\r\nDESCRIPTION:Reminder\r\nEND:VALARM\r\n"
        "END:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    src.create_event(text)
    updated = src.update_event("rich-1", EventPatch(title="Dentist (moved)"))
    assert updated.title == "Dentist (moved)"

    raw = (
        DAVClient(url=radicale, username="eric", password="secret")
        .calendar(url=f"{radicale}eric/home/")
        .get_event_by_uid("rich-1")
        .data
    )
    assert "BEGIN:VALARM" in raw
    assert "CATEGORIES:Health" in raw
    assert "PARTSTAT=ACCEPTED" in raw
    assert "SEQUENCE:3" in raw


def test_concurrent_edit_is_a_conflict(src: CalDAVSource, radicale: str) -> None:
    uid = create(src)
    stale = src._fetch(uid)
    src.update_event(uid, EventPatch(title="first"))
    with pytest.raises(ConflictError), src._errors(uid):
        with stale.edit_icalendar_instance() as cal:
            ical.apply_patch(cal, EventPatch(title="second"), "UTC")
        stale.save()
    assert src.get_event(uid).title == "first"


def test_create_does_not_overwrite(src: CalDAVSource) -> None:
    uid = create(src)
    start = datetime.now(UTC) + timedelta(days=5)
    _, text = ical.build_ical(
        EventCreate(title="dup", start_time=start, end_time=start + timedelta(hours=1)), "UTC", uid=uid
    )
    with pytest.raises(Exception):
        src.create_event(text)
    assert src.get_event(uid).title == "Lunch"


def test_delete(src: CalDAVSource) -> None:
    uid = create(src)
    src.delete_event(uid)
    with pytest.raises(EventNotFoundError):
        src.get_event(uid)
    with pytest.raises(EventNotFoundError):
        src.delete_event(uid)


def test_new_event_is_sequence_zero(src: CalDAVSource) -> None:
    uid = create(src)
    assert "SEQUENCE:0" in src._fetch(uid).data
