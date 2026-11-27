"""MCP adapter: the same calendar operations as MCP tools.

Served over streamable HTTP at /mcp by the main app, or over stdio by
`kairos mcp-stdio` for local clients.
"""

from __future__ import annotations

from collections.abc import Awaitable
from datetime import UTC, date, datetime
from typing import Any, TypeVar

from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import AnyHttpUrl

from kairos import __version__
from kairos.auth import TokenVerifier
from kairos.errors import KairosError
from kairos.models import AttendeeIn, EventCreate, EventPatch, EventStatus
from kairos.resolver import ServiceResolver, current_token, static_resolver
from kairos.service import CalendarService, event_to_dict, parse_timezone
from kairos.settings import Settings

T = TypeVar("T")

INSTRUCTIONS = (
    "Calendar tools backed by the user's own CalDAV calendars (iCloud, Nextcloud, Google). "
    "To answer what's on the user's calendar, call list_events or daily_brief without a calendar_id: "
    "that reads every calendar at once and labels each event with its calendar. "
    "Call list_calendars for calendar ids, which writes and get_event need. "
    "Times you send must include a UTC offset. "
    "Pass the user's IANA timezone to read tools so results come back in local time. "
    "One-off events that start within the lock window or have already started can't be changed. "
    "Confirm with the user before deleting events or inviting attendees: the calendar server may email them."
)

READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True)
UPDATE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=True)
DELETE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=True)


async def _run[T](coro: Awaitable[T]) -> T:
    try:
        return await coro
    except KairosError as exc:
        raise ToolError(f"{exc.code}: {exc}") from exc


def _dt(value: str | None, name: str) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        try:
            d = date.fromisoformat(value)
        except ValueError as exc:
            raise ToolError(f"invalid_request: {name} must be ISO 8601, got {value!r}") from exc
        parsed = datetime(d.year, d.month, d.day)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _attendees(raw: list[dict[str, Any]] | None) -> list[AttendeeIn] | None:
    if raw is None:
        return None
    try:
        return [AttendeeIn.model_validate(a) for a in raw]
    except ValueError as exc:
        raise ToolError(f"invalid_request: attendees: {exc}") from exc


def build_mcp(
    service: CalendarService | None,
    settings: Settings,
    verifier: TokenVerifier | None = None,
    *,
    resolver: ServiceResolver | None = None,
) -> MCPServer:
    """Build the MCP server.

    Pass a verifier only when an OIDC issuer is configured: the SDK then guards /mcp and
    publishes Protected Resource Metadata pointing at that issuer. Static-token-only
    setups are guarded by the app's own middleware instead (see kairos.app).

    Tools answer from `service`, or, when a resolver is given, from the service it
    returns for the caller's token on each call.
    """
    if resolver is None:
        if service is None:
            raise ValueError("build_mcp needs a service or a resolver")
        resolver = static_resolver(service)
    resolve = resolver

    async def svc() -> CalendarService:
        return await _run(resolve(current_token()))

    auth = None
    if verifier is not None:
        if settings.oidc is None or settings.mcp_resource_url is None:
            raise ValueError("SDK auth for /mcp needs KAIROS_OIDC_ISSUER and KAIROS_PUBLIC_URL")
        auth = AuthSettings(
            issuer_url=AnyHttpUrl(settings.oidc.issuer),
            resource_server_url=AnyHttpUrl(settings.mcp_resource_url),
            required_scopes=list(settings.oidc.required_scopes) or None,
            validate_token_resource=False,  # the verifier checks `aud` itself
        )

    mcp = MCPServer(
        name="kairos-mcp",
        title="Kairos calendars",
        description="Read and write your CalDAV calendars: events, daily brief and free slots.",
        instructions=INSTRUCTIONS,
        version=__version__,
        token_verifier=verifier,
        auth=auth,
    )

    @mcp.tool(annotations=READ)
    async def list_calendars() -> dict[str, Any]:
        """List the configured calendars with their ids, names, time zones and whether they're writable.

        You need a calendar_id to create, change or delete events. To read events you don't:
        list_events and daily_brief read every calendar when you leave calendar_id out.
        """
        return {
            "calendars": [
                {"id": c.id, "name": c.name, "writable": c.writable, "timezone": c.timezone}
                for c in (await svc()).calendars()
            ]
        }

    @mcp.tool(annotations=READ)
    async def list_events(
        calendar_id: str | None = None,
        date_min: str | None = None,
        date_max: str | None = None,
        limit: int = 100,
        timezone: str | None = None,
    ) -> dict[str, Any]:
        """List events between date_min and date_max (ISO 8601; default now to 30 days out, max 366 days).

        Omit calendar_id to read all calendars at once: each event then carries calendar_id
        and calendar_name, and any calendar that couldn't be read is listed under errors.
        Recurring events come back as one entry per occurrence. Pass timezone (IANA name,
        e.g. America/New_York) to get times in that zone instead of UTC. Returns event uids,
        which get_event, update_event and delete_event need.
        """
        tz = parse_timezone(timezone) if timezone else None
        start, end, cap = _dt(date_min, "date_min"), _dt(date_max, "date_max"), max(1, min(limit, 1000))
        if not calendar_id:
            pairs, total, errors = await _run((await svc()).list_all_events(start, end, cap))
            return {
                "calendar_id": None,
                "matched_count": total,
                "truncated": total > len(pairs),
                "events": [
                    {**event_to_dict(e, tz), "calendar_id": info.id, "calendar_name": info.name} for info, e in pairs
                ],
                "errors": errors,
            }
        events, matched = await _run((await svc()).list_events(calendar_id, start, end, cap))
        return {
            "calendar_id": calendar_id,
            "matched_count": matched,
            "truncated": matched > len(events),
            "events": [event_to_dict(e, tz) for e in events],
        }

    @mcp.tool(annotations=READ)
    async def get_event(calendar_id: str, uid: str, timezone: str | None = None) -> dict[str, Any]:
        """Get one event by uid. For a recurring event this returns the series."""
        tz = parse_timezone(timezone) if timezone else None
        return event_to_dict(await _run((await svc()).get_event(calendar_id, uid)), tz)

    @mcp.tool(annotations=READ)
    async def daily_brief(calendar_id: str | None = None, timezone: str | None = None) -> dict[str, Any]:
        """Today's events, free slots longer than 15 minutes, busy time and who you're meeting.

        Omit calendar_id to cover all calendars: free slots then account for every calendar,
        and each event carries calendar_id and calendar_name. timezone is an IANA name; it
        defaults to the calendar's configured zone (the first calendar's, across all). Use this
        for morning briefings and "what's on today" or "when am I free today" questions.
        """
        if not calendar_id:
            return await _run((await svc()).daily_brief_all(timezone))
        return await _run((await svc()).daily_brief(calendar_id, timezone))

    @mcp.tool(annotations=WRITE)
    async def create_event(
        calendar_id: str,
        title: str,
        start_time: str,
        end_time: str,
        description: str | None = None,
        location: str | None = None,
        all_day: bool = False,
        attendees: list[dict[str, Any]] | None = None,
        recurrence_rule: str | None = None,
        timezone: str | None = None,
        status: EventStatus = "confirmed",
    ) -> dict[str, Any]:
        """Create an event and return it, including its uid.

        start_time and end_time are ISO 8601 with a UTC offset (e.g. 2026-08-20T09:00:00-04:00).
        attendees is a list of {"email": ..., "name": ...}; the calendar server may send them
        invitations. recurrence_rule is an RFC 5545 RRULE such as FREQ=WEEKLY;BYDAY=MO,WE,FR.
        timezone (IANA) sets the zone the event is stored in; default is the calendar's zone.
        """
        try:
            body = EventCreate(
                title=title,
                start_time=_dt(start_time, "start_time"),  # type: ignore[arg-type]
                end_time=_dt(end_time, "end_time"),  # type: ignore[arg-type]
                description=description,
                location=location,
                all_day=all_day,
                attendees=_attendees(attendees) or [],
                recurrence_rule=recurrence_rule,
                timezone=timezone,
                status=status,
            )
        except ValueError as exc:
            raise ToolError(f"invalid_request: {exc}") from exc
        return event_to_dict(await _run((await svc()).create_event(calendar_id, body)))

    @mcp.tool(annotations=UPDATE)
    async def update_event(
        calendar_id: str,
        uid: str,
        title: str | None = None,
        description: str | None = None,
        location: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        all_day: bool | None = None,
        attendees: list[dict[str, Any]] | None = None,
        status: EventStatus | None = None,
    ) -> dict[str, Any]:
        """Change an event. Only the fields you pass change; everything else, including alarms
        and RSVPs, stays as it is. Pass an empty string to clear description or location.

        Moving only start_time keeps the event's length. attendees replaces the whole list.
        A recurring event's times can't be changed, and a one-off event can't be changed once
        it's about to start or has started.
        """
        fields = {
            "title": title,
            "description": description,
            "location": location,
            "start_time": _dt(start_time, "start_time"),
            "end_time": _dt(end_time, "end_time"),
            "all_day": all_day,
            "attendees": _attendees(attendees),
            "status": status,
        }
        try:
            patch = EventPatch.model_validate({k: v for k, v in fields.items() if v is not None})
        except ValueError as exc:
            raise ToolError(f"invalid_request: {exc}") from exc
        return event_to_dict(await _run((await svc()).update_event(calendar_id, uid, patch)))

    @mcp.tool(annotations=DELETE)
    async def delete_event(calendar_id: str, uid: str) -> dict[str, Any]:
        """Permanently delete an event. For a recurring event this deletes the whole series.

        Confirm with the user first; this can't be undone.
        """
        await _run((await svc()).delete_event(calendar_id, uid))
        return {"uid": uid, "status": "deleted"}

    @mcp.tool(annotations=READ)
    async def gateway_health() -> dict[str, Any]:
        """Connection and circuit-breaker status for each calendar source. Use it to diagnose failures."""
        return (await svc()).health()

    return mcp
