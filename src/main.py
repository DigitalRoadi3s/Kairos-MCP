"""
CalDAV REST Gateway — FastAPI entrypoint.

Phase 1 scaffold (task 1). Other modules (config, caldav_client, routers)
are wired in as their tasks land — see caldav-gateway-subagent-plan.md.
"""
import signal
import sys

from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(
    title="CalDAV REST Gateway",
    version="0.1.0",
    description="Exposes CalDAV calendar systems as a REST API.",
)


@app.get("/health")
async def health():
    """
    Basic liveness probe.

    TODO(sonnet, task 2/task 4): replace static stub with real per-calendar
    connection status once config.py and caldav_client.py land — see FR-7
    in the PRD for the target response shape:
        {"status": "healthy", "calendars": {"<id>": {"status": ..., ...}}}
    """
    return JSONResponse({"status": "healthy", "calendars": {}})


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
