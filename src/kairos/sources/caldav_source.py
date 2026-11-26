"""One configured CalDAV calendar, wrapped with error mapping.

This class is synchronous because the caldav library is. The service runs
every call in a worker thread so the event loop never blocks on CalDAV.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from caldav.collection import Calendar as DAVCalendar
from caldav.davclient import DAVClient
from caldav.lib import error as dav_error

from kairos import ical
from kairos.errors import (
    ConflictError,
    EventNotFoundError,
    InvalidRequestError,
    KairosError,
    SourceUnavailableError,
    UpstreamAuthError,
)
from kairos.models import Event, EventPatch
from kairos.settings import SourceConfig
from kairos.sources.google_oauth import GoogleOAuth2Auth, GoogleOAuthError

logger = logging.getLogger("kairos.source")

_CONFLICT_ERRORS: tuple[type[Exception], ...] = tuple(
    cls
    for cls in (
        getattr(dav_error, "ETagMismatchError", None),
        getattr(dav_error, "ScheduleTagMismatchError", None),
    )
    if cls is not None
)


class CalDAVSource:
    def __init__(self, config: SourceConfig) -> None:
        self.config = config
        self.id = config.id
        self.tz = ZoneInfo(config.timezone)
        self._client: Any = None
        self._calendar: Any = None

    # ------------------------------------------------------------------ plumbing

    @contextmanager
    def _errors(self, uid: str | None = None) -> Iterator[None]:
        """Translate caldav, HTTP and OAuth failures into Kairos errors."""
        try:
            yield
        except KairosError:
            raise
        except _CONFLICT_ERRORS as exc:
            raise ConflictError("The event changed on the server since it was read. Fetch it again and retry.") from exc
        except dav_error.RateLimitError as exc:
            raise SourceUnavailableError(f"Rate limited by the CalDAV server for {self.id!r}.") from exc
        except dav_error.AuthorizationError as exc:
            # iCloud also answers 403 when it throttles, so log the status for diagnosis.
            logger.error("caldav_auth_failed", extra={"source_id": self.id, "status": getattr(exc, "status", None)})
            raise UpstreamAuthError(self._auth_hint()) from exc
        except dav_error.NotFoundError as exc:
            if uid is not None:
                raise EventNotFoundError(f"Event {uid!r} not found") from exc
            raise SourceUnavailableError(
                f"Calendar for {self.id!r} not found at {self.config.calendar_path!r}. "
                "Check calendar_path with the discovery script in the README."
            ) from exc
        except dav_error.PutError as exc:
            raise InvalidRequestError(f"The CalDAV server refused the event: {exc}") from exc
        except GoogleOAuthError as exc:
            if exc.transient:
                raise SourceUnavailableError(str(exc)) from exc
            raise UpstreamAuthError(str(exc)) from exc
        except dav_error.DAVError as exc:
            raise SourceUnavailableError(f"CalDAV error from {self.id!r}: {exc}") from exc
        except OSError as exc:  # requests/niquests connection, timeout and TLS errors all derive from OSError
            raise SourceUnavailableError(f"Could not reach the CalDAV server for {self.id!r}: {exc}") from exc

    def _auth_hint(self) -> str:
        if self.config.auth_type == "oauth2":
            return (
                f"The CalDAV server rejected the OAuth token for {self.id!r}; the refresh token may be "
                "revoked or missing the calendar scope."
            )
        return (
            f"The CalDAV server rejected the credentials for {self.id!r}. iCloud needs an "
            "app-specific password, not the Apple ID password."
        )

    @property
    def connected(self) -> bool:
        return self._calendar is not None

    def _require_calendar(self) -> Any:
        if self._calendar is None:
            self.connect()
        return self._calendar

    # ------------------------------------------------------------------ lifecycle

    def connect(self) -> None:
        cfg = self.config
        with self._errors():
            if cfg.auth_type == "oauth2":
                assert cfg.client_id and cfg.client_secret and cfg.refresh_token
                auth = GoogleOAuth2Auth(cfg.client_id, cfg.client_secret, cfg.refresh_token, timeout=cfg.timeout)
                client = DAVClient(url=cfg.url, auth=auth, timeout=cfg.timeout, rate_limit_handle=False)
            else:
                client = DAVClient(
                    url=cfg.url,
                    username=cfg.username,
                    password=cfg.password,
                    timeout=cfg.timeout,
                    rate_limit_handle=False,
                )
            if cfg.calendar_path:
                calendar = DAVCalendar(client=client, url=cfg.calendar_path)
                calendar.get_display_name()  # one PROPFIND proves auth and path
            else:
                calendars = client.get_calendars()
                if not calendars:
                    raise SourceUnavailableError(f"No calendars found for source {self.id!r}")
                calendar = calendars[0]
        self._client, self._calendar = client, calendar
        logger.info("caldav_source_connected", extra={"source_id": self.id})

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                logger.warning("caldav_close_failed", extra={"source_id": self.id})
        self._client = self._calendar = None

    # ------------------------------------------------------------------ operations

    def list_events(self, start: datetime, end: datetime) -> list[Event]:
        calendar = self._require_calendar()
        with self._errors():
            objects = calendar.search(event=True, start=start, end=end, expand=True)
            events = []
            for obj in objects:
                for component in obj.get_icalendar_instance().walk("VEVENT"):
                    events.append(ical.parse_component(component, self.tz))
        events.sort(key=lambda e: (e.start_time, e.uid))
        return events

    def _fetch(self, uid: str) -> Any:
        calendar = self._require_calendar()
        with self._errors(uid):
            return calendar.get_event_by_uid(uid)

    def get_event(self, uid: str) -> Event:
        obj = self._fetch(uid)
        with self._errors(uid):
            return ical.parse_component(ical.master_component(obj.get_icalendar_instance()), self.tz)

    def create_event(self, ical_text: str) -> Event:
        calendar = self._require_calendar()
        with self._errors():
            # A new event is revision 0; add_event would otherwise bump SEQUENCE before the first PUT.
            calendar.add_event(ical_text, no_overwrite=True, increase_seqno=False)
        return ical.parse_ical(ical_text, self.tz)

    def update_event(self, uid: str, patch: EventPatch) -> Event:
        obj = self._fetch(uid)
        with self._errors(uid):
            with obj.edit_icalendar_instance() as cal:
                vevent = ical.apply_patch(cal, patch, self.config.timezone)
                event = ical.parse_component(vevent, self.tz)
            obj.save()
        return event

    def delete_event(self, uid: str) -> None:
        obj = self._fetch(uid)
        with self._errors(uid):
            obj.delete()
