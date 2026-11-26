"""REST adapter: the 1.x HTTP API, now a thin layer over CalendarService."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from kairos.errors import InvalidRequestError
from kairos.models import Event, EventCreate, EventPatch
from kairos.observability import render
from kairos.service import CalendarService, event_to_dict

router = APIRouter()
public = APIRouter()


def get_service(request: Request) -> CalendarService:
    service: CalendarService = request.app.state.service
    return service


async def require_auth(request: Request) -> None:
    settings = request.app.state.settings
    if settings.insecure_no_auth:
        return
    verifier = request.app.state.verifier
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "Missing bearer token", headers={"WWW-Authenticate": "Bearer"})
    if not verifier.configured:
        raise HTTPException(401, "No API tokens are configured on this server", headers={"WWW-Authenticate": "Bearer"})
    if await verifier.verify_token(token.strip()) is None:
        raise HTTPException(401, "Invalid bearer token", headers={"WWW-Authenticate": "Bearer"})


Service = Annotated[CalendarService, Depends(get_service)]


def _parse_dt(value: str | None, name: str) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        try:
            d = date.fromisoformat(value)
        except ValueError as exc:
            raise InvalidRequestError(f"Invalid {name}: {value!r} is not ISO 8601") from exc
        parsed = datetime(d.year, d.month, d.day)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _write_result(e: Event, calendar_id: str) -> dict[str, Any]:
    return {
        **event_to_dict(e),
        "created_at": e.last_modified.isoformat() if e.last_modified else None,
        "link": f"/api/v1/calendars/{calendar_id}/events/{e.uid}",
    }


@public.get("/health")
async def health(service: Service) -> JSONResponse:
    body = service.health()
    return JSONResponse(body, status_code=200 if body["status"] == "healthy" else 503)


@public.get("/metrics")
async def metrics() -> Response:
    body, content_type = render()
    return Response(content=body, media_type=content_type)


@router.get("/api/v1/calendars")
async def list_calendars(service: Service) -> dict[str, Any]:
    return {
        "calendars": [
            {"id": c.id, "name": c.name, "writable": c.writable, "timezone": c.timezone} for c in service.calendars()
        ]
    }


@router.get("/api/v1/calendars/{calendar_id}/events", summary="List events in a date range")
async def list_events(
    service: Service,
    calendar_id: str,
    date_min: str | None = Query(None, description="ISO 8601; default now"),
    date_max: str | None = Query(None, description="ISO 8601; default date_min + 30 days"),
    limit: int = Query(100, ge=1, le=1000),
) -> dict[str, Any]:
    info = service.calendar(calendar_id)
    events, matched = await service.list_events(
        calendar_id, _parse_dt(date_min, "date_min"), _parse_dt(date_max, "date_max"), limit
    )
    return {
        "calendar_id": calendar_id,
        "calendar_name": info.name,
        "total_count": len(events),
        "matched_count": matched,
        "truncated": matched > len(events),
        "events": [event_to_dict(e) for e in events],
    }


@router.get("/api/v1/calendars/{calendar_id}/events/{uid}", summary="Get one event")
async def get_event(service: Service, calendar_id: str, uid: str) -> dict[str, Any]:
    return event_to_dict(await service.get_event(calendar_id, uid))


@router.post("/api/v1/calendars/{calendar_id}/events", status_code=201)
async def create_event(service: Service, calendar_id: str, body: EventCreate) -> dict[str, Any]:
    return _write_result(await service.create_event(calendar_id, body), calendar_id)


@router.put("/api/v1/calendars/{calendar_id}/events/{uid}")
@router.patch("/api/v1/calendars/{calendar_id}/events/{uid}")
async def update_event(service: Service, calendar_id: str, uid: str, body: EventPatch) -> dict[str, Any]:
    return _write_result(await service.update_event(calendar_id, uid, body), calendar_id)


@router.delete("/api/v1/calendars/{calendar_id}/events/{uid}")
async def delete_event(service: Service, calendar_id: str, uid: str) -> dict[str, Any]:
    await service.delete_event(calendar_id, uid)
    return {"uid": uid, "status": "deleted", "deleted_at": datetime.now(UTC).isoformat()}


@router.get("/api/v1/calendars/{calendar_id}/today", summary="Daily brief: today's events and free slots")
async def today(service: Service, calendar_id: str, tz: str | None = Query(None, alias="timezone")) -> dict[str, Any]:
    return await service.daily_brief(calendar_id, tz)
