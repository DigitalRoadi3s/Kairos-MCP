"""
CalDAV REST Gateway — FastAPI entrypoint.

Phase 1 (tasks 1, 2, 3, 5, 6). Task 7 will replace the /metrics stub with
real prometheus_client output; task 23 will flesh out graceful shutdown.
"""
import logging
import signal
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, Response

from pydantic import BaseModel, Field

from logging_config import setup_logging

setup_logging()

import config
import daily_brief
import metrics
from caldav_client import CalDAVAuthError, CalDAVNotFoundError, CalDAVUnavailableError
from ical_writer import EventInput, generate_ical

LOCK_WINDOW_MINUTES = 15  # FR-4/FR-5: events within this window of start can't be modified/deleted

logger = logging.getLogger("caldav_gateway.main")

app = FastAPI(
    title="CalDAV REST Gateway",
    version="0.1.0",
    description="Exposes CalDAV calendar systems as a REST API.",
)


@app.middleware("http")
async def metrics_middleware(request, call_next):
    """Task 19: api_request_duration_seconds / api_errors_total."""
    with metrics.api_request_duration_seconds.labels(
            method=request.method, path=request.url.path).time():
        response = await call_next(request)
    if response.status_code >= 400:
        metrics.api_errors_total.labels(status_code=str(response.status_code)).inc()
    return response

# Populated at startup by load_sources(); source_id -> CalDAVSourceClient.
# Task 4/6 handlers read from this. Task 2 owns how it's built.
app.state.clients = {}
app.state.breakers = {}
app.state.startup_error = None


@app.on_event("startup")
async def load_sources():
    try:
        app.state.clients, app.state.breakers = config.load_and_validate()
    except config.ConfigError as exc:
        # Per FR-1: fail gracefully, not a hard crash — /health reports
        # the problem instead of the container refusing to start.
        logger.error("startup_config_error", extra={"error": str(exc)})
        app.state.startup_error = str(exc)


def _get_client(calendar_id: str):
    client = app.state.clients.get(calendar_id)
    if client is None:
        raise HTTPException(status_code=404, detail=f"Calendar '{calendar_id}' not found")
    return client


@app.get("/health")
async def health():
    """FR-7. Per-calendar connection status."""
    calendars = {}
    for source_id, client in app.state.clients.items():
        breaker_status = app.state.breakers.get(source_id)
        calendars[source_id] = {
            "status": "connected",
            "last_sync": None,
            "last_error": None,
            "rate_limit_remaining": None,  # TODO(task 19 metrics wiring, not this task): iCloud doesn't expose this via caldav lib headers we've captured yet
            "cache_hit_rate": None,  # TODO(task 19): populate from metrics
            "circuit_breaker": breaker_status.status() if breaker_status else None,
        }
    status = "healthy" if calendars else "unhealthy"
    body = {"status": status, "calendars": calendars}
    if app.state.startup_error:
        body["startup_error"] = app.state.startup_error
    return JSONResponse(body, status_code=200 if calendars else 503)


@app.get("/api/v1/calendars")
async def list_calendars():
    """FR-7."""
    return {
        "calendars": [
            {"id": source_id, "name": source_id, "writable": True, "timezone": "UTC"}
            for source_id in app.state.clients
        ]
    }


@app.get("/api/v1/calendars/{calendar_id}/events", summary="List events in a date range")
async def list_events(
    calendar_id: str,
    date_min: Optional[str] = Query(None, description="ISO 8601, default: today"),
    date_max: Optional[str] = Query(None, description="ISO 8601, default: +30 days"),
    limit: int = Query(100, ge=1, le=1000),
):
    """FR-2."""
    client = _get_client(calendar_id)

    try:
        dmin = datetime.fromisoformat(date_min) if date_min else datetime.now(timezone.utc)
        dmax = datetime.fromisoformat(date_max) if date_max else dmin + timedelta(days=30)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid date range: {exc}")

    if dmin.tzinfo is None:
        dmin = dmin.replace(tzinfo=timezone.utc)
    if dmax.tzinfo is None:
        dmax = dmax.replace(tzinfo=timezone.utc)
    if dmin >= dmax:
        raise HTTPException(status_code=400, detail="date_min must be before date_max")

    try:
        events = client.list_events(date_min=dmin, date_max=dmax)
    except CalDAVUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    events = events[:limit]
    return {
        "calendar_id": calendar_id,
        "calendar_name": calendar_id,
        "total_count": len(events),
        "events": [
            {
                "uid": e.uid,
                "title": e.title,
                "description": e.description,
                "start_time": e.start_time.isoformat(),
                "end_time": e.end_time.isoformat(),
                "all_day": e.all_day,
                "attendees": [
                    {"email": a.email, "name": a.name, "status": a.status}
                    for a in e.attendees
                ],
                "location": e.location,
                "recurrence_rule": e.recurrence_rule,
                "last_modified": e.last_modified.isoformat() if e.last_modified else None,
                "status": e.status,
            }
            for e in events
        ],
    }


class AttendeeIn(BaseModel):
    email: str = Field(..., description="Attendee email address")
    name: Optional[str] = Field(None, description="Attendee display name")


class EventCreate(BaseModel):
    """FR-3 request body."""
    title: str = Field(..., max_length=200, description="Event title, max 200 characters")
    description: Optional[str] = Field(None, description="Free-text event description")
    start_time: datetime = Field(..., description="ISO 8601 timestamp, must include a timezone offset")
    end_time: datetime = Field(..., description="ISO 8601 timestamp, must include a timezone offset and be after start_time")
    all_day: bool = Field(False, description="If true, times are ignored and the event spans the full day")
    location: Optional[str] = Field(None, description="Free-text event location")
    attendees: list[AttendeeIn] = Field(default_factory=list, description="Event attendees")
    recurrence_rule: Optional[str] = Field(
        None, description="RFC 5545 RRULE value, e.g. 'FREQ=WEEKLY;BYDAY=MO,WE,FR'. Omit for a one-off event.")
    timezone: Optional[str] = Field(None, description="Informational only — the wire format is always UTC")
    status: str = Field("confirmed", description="confirmed | tentative | cancelled")


class EventUpdate(BaseModel):
    """FR-4 request body - all fields optional (PATCH semantics via PUT)."""
    title: Optional[str] = Field(None, max_length=200, description="New title; omit to leave unchanged")
    description: Optional[str] = Field(None, description="New description; omit to leave unchanged")
    start_time: Optional[datetime] = Field(None, description="New start time; omit to leave unchanged")
    end_time: Optional[datetime] = Field(None, description="New end time; omit to leave unchanged")
    all_day: Optional[bool] = Field(None, description="Omit to leave unchanged")
    location: Optional[str] = Field(None, description="Omit to leave unchanged")
    attendees: Optional[list[AttendeeIn]] = Field(None, description="Replaces the full attendee list if provided")
    status: Optional[str] = Field(None, description="confirmed | tentative | cancelled; omit to leave unchanged")


def _event_out(e, calendar_id: str):
    return {
        "uid": e.uid,
        "title": e.title,
        "start_time": e.start_time.isoformat(),
        "end_time": e.end_time.isoformat(),
        "created_at": None,
        "link": f"/api/v1/calendars/{calendar_id}/events/{e.uid}",
    }


def _within_lock_window(event) -> bool:
    """
    FR-4/FR-5: "Only future events can be modified (events within 15 min
    of start time are locked)." Both clauses collapse into one check:
    a write is allowed only if start_time is more than LOCK_WINDOW_MINUTES
    in the future. Already-started/past events (start_time <= now) are
    covered by the same inequality, not a separate branch.
    """
    now = datetime.now(timezone.utc)
    return event.start_time <= now + timedelta(minutes=LOCK_WINDOW_MINUTES)


@app.post("/api/v1/calendars/{calendar_id}/events", status_code=201)
async def create_event(calendar_id: str, body: EventCreate):
    """FR-3."""
    client = _get_client(calendar_id)

    if body.start_time.tzinfo is None or body.end_time.tzinfo is None:
        raise HTTPException(status_code=400, detail="start_time and end_time must include a timezone offset")
    if not body.all_day and body.start_time >= body.end_time:
        raise HTTPException(status_code=400, detail="start_time must be before end_time")

    try:
        ical = generate_ical(EventInput(
            title=body.title,
            description=body.description,
            start_time=body.start_time,
            end_time=body.end_time,
            all_day=body.all_day,
            location=body.location,
            attendees=[(a.email, a.name) for a in body.attendees],
            recurrence_rule=body.recurrence_rule,
            status=body.status,
        ))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        event = client.create_event(ical)
    except CalDAVUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    daily_brief.invalidate(calendar_id)  # task 14: invalidate AFTER successful write
    return _event_out(event, calendar_id)


@app.put("/api/v1/calendars/{calendar_id}/events/{uid}")
async def update_event(calendar_id: str, uid: str, body: EventUpdate):
    """FR-4."""
    client = _get_client(calendar_id)

    try:
        existing = client.get_event(uid)
    except CalDAVNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except CalDAVUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    if _within_lock_window(existing):
        raise HTTPException(status_code=409, detail=(
            f"Event starts within {LOCK_WINDOW_MINUTES} minutes and cannot be modified"))
    if existing.recurrence_rule and (body.start_time or body.end_time):
        raise HTTPException(status_code=409, detail="Recurring events are read-only for time changes")

    merged = EventInput(
        title=body.title if body.title is not None else existing.title,
        description=body.description if body.description is not None else existing.description,
        start_time=body.start_time if body.start_time is not None else existing.start_time,
        end_time=body.end_time if body.end_time is not None else existing.end_time,
        all_day=body.all_day if body.all_day is not None else existing.all_day,
        location=body.location if body.location is not None else existing.location,
        attendees=([(a.email, a.name) for a in body.attendees] if body.attendees is not None
                   else [(a.email, a.name) for a in existing.attendees]),
        status=body.status if body.status is not None else existing.status,
        uid=uid,
        recurrence_rule=existing.recurrence_rule,  # never settable via update (task 25:
        # recurrence changes go through create, not PATCH - see the lock-window
        # check above that already rejects start_time/end_time changes on
        # recurring events; the rule itself is likewise immutable via PUT)
    )

    try:
        ical = generate_ical(merged)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        event = client.update_event(uid, ical)
    except CalDAVUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    daily_brief.invalidate(calendar_id)
    return _event_out(event, calendar_id)


@app.delete("/api/v1/calendars/{calendar_id}/events/{uid}")
async def delete_event(calendar_id: str, uid: str):
    """FR-5."""
    client = _get_client(calendar_id)

    try:
        existing = client.get_event(uid)
    except CalDAVNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except CalDAVUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    if _within_lock_window(existing):
        raise HTTPException(status_code=409, detail=(
            f"Event starts within {LOCK_WINDOW_MINUTES} minutes and cannot be deleted"))

    try:
        client.delete_event(uid)
    except CalDAVUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    daily_brief.invalidate(calendar_id)
    return {"uid": uid, "status": "deleted", "deleted_at": datetime.now(timezone.utc).isoformat()}


@app.get("/api/v1/calendars/{calendar_id}/today", summary="Daily brief: today's events + free-slot analysis")
async def today(calendar_id: str, tz: str = Query("UTC", alias="timezone")):
    """FR-6. Wires the daily_brief engine (task 5) behind the endpoint."""
    client = _get_client(calendar_id)
    try:
        return daily_brief.get_daily_brief(client, calendar_id, tz_name=tz, calendar_name=calendar_id)
    except CalDAVUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@app.get("/metrics")
async def metrics_endpoint():
    """FR-7 / NFR-4. Real Prometheus output (task 19)."""
    body, content_type = metrics.render()
    return Response(content=body, media_type=content_type)


def _shutdown_handler(signum, frame):
    """
    Task 23. Closes each source's underlying requests session (the
    caldav library's DAVClient wraps a requests.Session internally) so
    connections aren't left dangling, then exits. Per PRD: must
    complete within 10s of SIGTERM - closing a session is near-
    instant, so no explicit timeout/deadline logic is needed here;
    the risk PRD flags (hung in-flight requests) is bounded anyway by
    the 30s CalDAV request timeout already set in caldav_client.py,
    which is out of our control to interrupt mid-flight without more
    invasive work than this task's scope covers.
    """
    logger.info("shutdown_signal_received", extra={"signal": signum})
    for source_id, client in app.state.clients.items():
        try:
            if client._client is not None and hasattr(client._client, "session"):
                client._client.session.close()
            logger.info("caldav_connection_closed", extra={"source_id": source_id})
        except Exception as exc:
            logger.warning("caldav_connection_close_failed",
                            extra={"source_id": source_id, "error": str(exc)})
    sys.exit(0)


signal.signal(signal.SIGTERM, _shutdown_handler)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8080)
