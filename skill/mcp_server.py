#!/usr/bin/env python3
"""
Kairos-MCP Server

Wraps every endpoint of Kairos-MCP as an MCP tool.
Designed for use with Claude Desktop, Claude Code, and OpenClaw.

Configuration:
  CALDAV_GATEWAY_URL  URL of the running gateway (default: http://localhost:8080)

Claude Desktop (add to claude_desktop_config.json → mcpServers):
  {
    "caldav": {
      "command": "python3",
      "args": ["/path/to/mcp_server.py"],
      "env": { "CALDAV_GATEWAY_URL": "http://localhost:8080" }
    }
  }

OpenClaw (add to your openclawconfig.json → mcp_servers):
  same structure — OpenClaw proxies stdio MCP servers directly.
"""
from __future__ import annotations

import json
import os
from typing import Optional

import httpx
from mcp.server.mcpserver import MCPServer

GATEWAY_URL = os.getenv("CALDAV_GATEWAY_URL", "http://localhost:8080").rstrip("/")

mcp = MCPServer(
    name="kairos-mcp",
    title="Kairos-MCP",
    description=(
        "Read and write calendar events across iCloud, Nextcloud, and Google Calendar "
        "via Kairos-MCP. Supports event listing, daily brief with free-slot "
        "analysis, and full CRUD including recurring events."
    ),
    version="1.0.0",
)


def _get(path: str, **params) -> dict:
    params = {k: v for k, v in params.items() if v is not None}
    r = httpx.get(f"{GATEWAY_URL}{path}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def _post(path: str, body: dict) -> dict:
    r = httpx.post(f"{GATEWAY_URL}{path}", json=body, timeout=30)
    r.raise_for_status()
    return r.json()


def _put(path: str, body: dict) -> dict:
    r = httpx.put(f"{GATEWAY_URL}{path}", json=body, timeout=30)
    r.raise_for_status()
    return r.json()


def _delete(path: str) -> dict:
    r = httpx.delete(f"{GATEWAY_URL}{path}", timeout=30)
    r.raise_for_status()
    return r.json()


def _fmt(data: dict) -> str:
    return json.dumps(data, indent=2, default=str)


@mcp.tool()
def list_calendars() -> str:
    """
    List all calendars configured in Kairos-MCP.
    Returns calendar IDs, names, and whether each is writable.
    Call this first to discover valid calendar_id values for other tools.
    """
    return _fmt(_get("/api/v1/calendars"))


@mcp.tool()
def list_events(
    calendar_id: str,
    date_min: Optional[str] = None,
    date_max: Optional[str] = None,
    limit: int = 100,
) -> str:
    """
    List events from a calendar in a date range.

    Args:
        calendar_id: The calendar ID from list_calendars (e.g. "icloud").
        date_min:    Start of range, ISO 8601 (e.g. "2026-08-01T00:00:00Z").
                     Defaults to today.
        date_max:    End of range, ISO 8601. Defaults to 30 days from date_min.
        limit:       Max events to return (1-1000, default 100).

    Returns JSON with the event list including UIDs, titles, times, attendees.
    UIDs are needed for update_event and delete_event.
    """
    return _fmt(_get(
        f"/api/v1/calendars/{calendar_id}/events",
        date_min=date_min,
        date_max=date_max,
        limit=limit,
    ))


@mcp.tool()
def daily_brief(
    calendar_id: str,
    timezone: str = "UTC",
) -> str:
    """
    Get today's calendar brief: all events for the day, free-slot analysis
    (gaps > 15 minutes where new meetings could be scheduled), total calendar
    time, and a ranked list of today's attendees.

    Args:
        calendar_id: The calendar ID from list_calendars.
        timezone:    IANA timezone name (e.g. "America/New_York", "Europe/London").
                     Times in the response are expressed in this zone.

    Use this for morning briefings, scheduling assistance, or any question
    about what's happening today.
    """
    return _fmt(_get(f"/api/v1/calendars/{calendar_id}/today", timezone=timezone))


@mcp.tool()
def create_event(
    calendar_id: str,
    title: str,
    start_time: str,
    end_time: str,
    description: Optional[str] = None,
    location: Optional[str] = None,
    all_day: bool = False,
    attendees: Optional[list[dict]] = None,
    recurrence_rule: Optional[str] = None,
    status: str = "confirmed",
) -> str:
    """
    Create a new calendar event.

    Args:
        calendar_id:     The calendar ID from list_calendars.
        title:           Event title (max 200 characters).
        start_time:      ISO 8601 with timezone offset (e.g. "2026-08-20T09:00:00-04:00").
        end_time:        ISO 8601 with timezone offset, must be after start_time.
        description:     Optional free-text description.
        location:        Optional location string.
        all_day:         True for all-day events (times are ignored if set).
        attendees:       Optional list of {"email": "...", "name": "..."} dicts.
        recurrence_rule: Optional RFC 5545 RRULE string for recurring events,
                         e.g. "FREQ=WEEKLY;BYDAY=MO,WE,FR" for a weekly standup
                         on Mon/Wed/Fri. Omit for a one-off event.
        status:          "confirmed" (default), "tentative", or "cancelled".

    Returns the created event's UID — save it if you need to update or delete later.
    Events starting within 15 minutes cannot be modified after creation.
    """
    body = {
        "title": title,
        "start_time": start_time,
        "end_time": end_time,
        "all_day": all_day,
        "status": status,
    }
    if description is not None:
        body["description"] = description
    if location is not None:
        body["location"] = location
    if attendees:
        body["attendees"] = attendees
    if recurrence_rule:
        body["recurrence_rule"] = recurrence_rule
    return _fmt(_post(f"/api/v1/calendars/{calendar_id}/events", body))


@mcp.tool()
def update_event(
    calendar_id: str,
    uid: str,
    title: Optional[str] = None,
    description: Optional[str] = None,
    location: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    all_day: Optional[bool] = None,
    attendees: Optional[list[dict]] = None,
    status: Optional[str] = None,
) -> str:
    """
    Update an existing calendar event. Uses PATCH semantics — only fields
    you provide are changed; omitted fields keep their current values.

    Args:
        calendar_id: The calendar ID from list_calendars.
        uid:         The event UID (from list_events or create_event).
        title:       New title, or omit to leave unchanged.
        description: New description, or omit to leave unchanged.
        location:    New location, or omit to leave unchanged.
        start_time:  New start time (ISO 8601 with offset), or omit to leave unchanged.
                     Cannot be changed on a recurring event.
        end_time:    New end time (ISO 8601 with offset), or omit to leave unchanged.
                     Cannot be changed on a recurring event.
        all_day:     Omit to leave unchanged.
        attendees:   New full attendee list (replaces the existing list), or omit.
        status:      "confirmed", "tentative", or "cancelled". Omit to leave unchanged.

    Note: events starting within 15 minutes are locked and cannot be updated.
    The recurrence_rule of a recurring event is immutable via update — delete
    and recreate if you need to change the recurrence pattern.
    """
    body = {}
    if title is not None:
        body["title"] = title
    if description is not None:
        body["description"] = description
    if location is not None:
        body["location"] = location
    if start_time is not None:
        body["start_time"] = start_time
    if end_time is not None:
        body["end_time"] = end_time
    if all_day is not None:
        body["all_day"] = all_day
    if attendees is not None:
        body["attendees"] = attendees
    if status is not None:
        body["status"] = status
    return _fmt(_put(f"/api/v1/calendars/{calendar_id}/events/{uid}", body))


@mcp.tool()
def delete_event(calendar_id: str, uid: str) -> str:
    """
    Delete a calendar event permanently.

    Args:
        calendar_id: The calendar ID from list_calendars.
        uid:         The event UID (from list_events or create_event).

    Note: events starting within 15 minutes are locked and cannot be deleted.
    Always confirm with the user before deleting — this cannot be undone.
    """
    return _fmt(_delete(f"/api/v1/calendars/{calendar_id}/events/{uid}"))


@mcp.tool()
def gateway_health() -> str:
    """
    Check the Kairos-MCP health status — shows each configured calendar
    source's connection state and circuit-breaker status.
    Use this to diagnose connection problems before other tools fail.
    """
    return _fmt(_get("/health"))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio
    asyncio.run(mcp.run_stdio_async())
