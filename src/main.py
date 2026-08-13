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
from fastapi.responses import JSONResponse

import config
import daily_brief
from caldav_client import CalDAVAuthError, CalDAVNotFoundError, CalDAVUnavailableError

logger = logging.getLogger("caldav_gateway.main")

app = FastAPI(
    title="CalDAV REST Gateway",
    version="0.1.0",
    description="Exposes CalDAV calendar systems as a REST API.",
)

# Populated at startup by load_sources(); source_id -> CalDAVSourceClient.
# Task 4/6 handlers read from this. Task 2 owns how it's built.
app.state.clients = {}
app.state.startup_error = None


@app.on_event("startup")
async def load_sources():
    try:
        app.state.clients = config.load_and_validate()
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
        calendars[source_id] = {
            "status": "connected",
            "last_sync": None,  # TODO(task 17): populate from circuit breaker state
            "last_error": None,
            "rate_limit_remaining": None,  # TODO(task 16): populate once tracked
            "cache_hit_rate": None,  # TODO(task 19): populate from metrics
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


@app.get("/api/v1/calendars/{calendar_id}/events")
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


@app.get("/api/v1/calendars/{calendar_id}/today")
async def today(calendar_id: str, tz: str = Query("UTC", alias="timezone")):
    """FR-6. Wires the daily_brief engine (task 5) behind the endpoint."""
    client = _get_client(calendar_id)
    try:
        return daily_brief.get_daily_brief(client, calendar_id, tz_name=tz, calendar_name=calendar_id)
    except CalDAVUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@app.get("/metrics")
async def metrics_stub():
    """
    TODO(haiku, task 19): replace with prometheus_client generate_latest()
    output once metrics are wired (caldav_request_duration_seconds,
    caldav_calendar_sync_errors_total, api_request_duration_seconds,
    api_errors_total, caldav_rate_limit_remaining).
    """
    return JSONResponse({"note": "metrics not yet wired — see task 19"})


def _shutdown_handler(signum, frame):
    """
    TODO(haiku, task 23): close CalDAV connection pool gracefully here
    once caldav_client.py exists. Must exit within 10s of SIGTERM.
    """
    print("Received SIGTERM, shutting down...", file=sys.stderr)
    sys.exit(0)


signal.signal(signal.SIGTERM, _shutdown_handler)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8080)
