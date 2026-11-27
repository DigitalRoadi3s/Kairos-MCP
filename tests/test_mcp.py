"""MCP over streamable HTTP: auth gate, tool calls, protected-resource metadata."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient

from kairos.app import create_app
from kairos.errors import EventNotFoundError
from kairos.service import CalendarService
from kairos.settings import OIDCConfig

from .conftest import AUTH, FakeSource, fast_breaker, future, make_settings

HEADERS = {**AUTH, "Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def rpc(
    client: TestClient, method: str, params: dict[str, Any] | None = None, headers: dict[str, str] = HEADERS
) -> Any:
    resp = client.post(
        "/mcp",
        headers=headers,
        content=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}),
    )
    return resp


def initialize(client: TestClient) -> None:
    resp = rpc(
        client,
        "initialize",
        {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}},
    )
    assert resp.status_code == 200, resp.text


def call(client: TestClient, tool: str, **args: Any) -> dict[str, Any]:
    resp = rpc(client, "tools/call", {"name": tool, "arguments": args})
    assert resp.status_code == 200, resp.text
    return resp.json()["result"]


def payload(result: dict[str, Any]) -> Any:
    if "structuredContent" in result and result["structuredContent"] is not None:
        return result["structuredContent"]
    return json.loads(result["content"][0]["text"])


def test_mcp_requires_token(client: TestClient) -> None:
    headers = {k: v for k, v in HEADERS.items() if k != "Authorization"}
    assert rpc(client, "initialize", headers=headers).status_code == 401


def test_tools_listed_with_annotations(client: TestClient) -> None:
    initialize(client)
    tools = {t["name"]: t for t in rpc(client, "tools/list").json()["result"]["tools"]}
    assert set(tools) == {
        "list_calendars",
        "list_events",
        "get_event",
        "daily_brief",
        "create_event",
        "update_event",
        "delete_event",
        "gateway_health",
    }
    assert tools["list_events"]["annotations"]["readOnlyHint"] is True
    assert tools["delete_event"]["annotations"]["destructiveHint"] is True


def test_create_list_update_delete(client: TestClient, home: FakeSource) -> None:
    initialize(client)
    created = payload(
        call(
            client,
            "create_event",
            calendar_id="home",
            title="Lunch",
            start_time=future().isoformat(),
            end_time=future(hours=1).isoformat(),
        )
    )
    uid = created["uid"]
    assert uid in home.store

    listed = payload(call(client, "list_events", calendar_id="home", timezone="Europe/Madrid"))
    assert any(e["uid"] == uid for e in listed["events"])
    assert listed["events"][0]["start_time"].endswith(("+01:00", "+02:00"))

    updated = payload(call(client, "update_event", calendar_id="home", uid=uid, title="Late lunch"))
    assert updated["title"] == "Late lunch"

    assert payload(call(client, "delete_event", calendar_id="home", uid=uid))["status"] == "deleted"
    assert uid not in home.store


def test_errors_come_back_as_tool_errors(client: TestClient) -> None:
    initialize(client)
    result = call(client, "get_event", calendar_id="home", uid="missing")
    assert result["isError"] is True
    assert "event_not_found" in result["content"][0]["text"]
    result = call(
        client,
        "create_event",
        calendar_id="work",
        title="x",
        start_time=future().isoformat(),
        end_time=future(hours=1).isoformat(),
    )
    assert "calendar_read_only" in result["content"][0]["text"]


def test_daily_brief_and_health(client: TestClient) -> None:
    initialize(client)
    assert payload(call(client, "daily_brief", calendar_id="home"))["timezone"] == "America/New_York"
    assert payload(call(client, "gateway_health"))["status"] == "healthy"


def test_protected_resource_metadata_with_oidc() -> None:
    settings = make_settings(
        public_url="https://cal.example.com",
        oidc=OIDCConfig(
            issuer="https://id.example.com", audiences=("https://cal.example.com/mcp",), required_scopes=("calendar",)
        ),
    )
    service = CalendarService(settings, FakeSource, breaker_factory=fast_breaker)
    with TestClient(create_app(settings, service)) as client:
        meta = client.get("/.well-known/oauth-protected-resource/mcp")
        assert meta.status_code == 200
        body = meta.json()
        assert body["resource"] == "https://cal.example.com/mcp"
        assert body["authorization_servers"] == ["https://id.example.com"]
        assert body["scopes_supported"] == ["calendar"]
        assert client.get("/.well-known/oauth-protected-resource").json() == body
        assert client.get("/.well-known/oauth-authorization-server").status_code == 404
        unauth = rpc(client, "initialize", headers={k: v for k, v in HEADERS.items() if k != "Authorization"})
        assert unauth.status_code == 401
        assert "resource_metadata=" in unauth.headers["www-authenticate"]
        # static tokens still work alongside OIDC
        initialize(client)


def test_workos_discovery() -> None:
    import httpx
    from fastapi import FastAPI

    from kairos.api.wellknown import build_router

    authkit = "https://example-123.authkit.app"
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={"issuer": authkit, "registration_endpoint": f"{authkit}/oauth2/register"})

    settings = make_settings(
        public_url="https://cal.example.com",
        oidc=OIDCConfig(
            issuer=authkit, audiences=("https://cal.example.com/mcp",), allowed_subjects=("user_1",), provider="workos"
        ),
    )
    app = FastAPI()
    app.include_router(build_router(settings, httpx.AsyncClient(transport=httpx.MockTransport(handler))))
    client = TestClient(app)
    prm = client.get("/.well-known/oauth-protected-resource/mcp").json()
    assert prm["authorization_servers"] == [authkit]
    assert "scopes_supported" not in prm
    meta = client.get("/.well-known/oauth-authorization-server")
    assert meta.json()["registration_endpoint"] == f"{authkit}/oauth2/register"
    client.get("/.well-known/oauth-authorization-server")
    assert calls == [f"{authkit}/.well-known/oauth-authorization-server"]  # cached


def test_workos_discovery_upstream_down() -> None:
    import httpx
    from fastapi import FastAPI

    from kairos.api.wellknown import build_router

    settings = make_settings(
        public_url="https://cal.example.com",
        oidc=OIDCConfig(issuer="https://x.authkit.app", audiences=("a",), allowed_subjects=("u",), provider="workos"),
    )
    app = FastAPI()
    transport = httpx.MockTransport(lambda r: httpx.Response(503))
    app.include_router(build_router(settings, httpx.AsyncClient(transport=transport)))
    assert TestClient(app).get("/.well-known/oauth-authorization-server").status_code == 502


def _event(source: FakeSource, uid: str, title: str, start: datetime, hours: float = 1) -> None:
    def stamp(value: datetime) -> str:
        return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")

    source.put_raw(
        "\r\n".join(
            [
                "BEGIN:VCALENDAR",
                "VERSION:2.0",
                "BEGIN:VEVENT",
                f"UID:{uid}",
                f"SUMMARY:{title}",
                f"DTSTART:{stamp(start)}",
                f"DTEND:{stamp(start + timedelta(hours=hours))}",
                "END:VEVENT",
                "END:VCALENDAR",
                "",
            ]
        )
    )


def _source(service: CalendarService, cid: str) -> FakeSource:
    return service._handles[cid].source  # type: ignore[return-value]


def test_list_events_without_calendar_id_reads_every_calendar(client: TestClient, service: CalendarService) -> None:
    _event(_source(service, "work"), "standup", "Standup", future(hours=1))
    _event(_source(service, "home"), "dentist", "Dentist", future(hours=3))
    _event(_source(service, "home"), "breakfast", "Breakfast", future())
    initialize(client)

    listed = payload(call(client, "list_events"))

    assert [(e["title"], e["calendar_name"]) for e in listed["events"]] == [
        ("Breakfast", "Home"),
        ("Standup", "Work"),
        ("Dentist", "Home"),
    ]
    assert listed["events"][1]["calendar_id"] == "work"
    assert listed["matched_count"] == 3
    assert listed["errors"] == []


def test_one_broken_calendar_doesnt_hide_the_others(client: TestClient, service: CalendarService) -> None:
    _event(_source(service, "home"), "dentist", "Dentist", future())
    _source(service, "work").fail_with = EventNotFoundError("gone")
    initialize(client)

    listed = payload(call(client, "list_events", limit=5))

    assert [e["title"] for e in listed["events"]] == ["Dentist"]
    assert listed["errors"] == [{"calendar_id": "work", "calendar_name": "Work", "error": "event_not_found: gone"}]


def test_reading_all_fails_when_every_calendar_fails(client: TestClient, service: CalendarService) -> None:
    for cid in ("home", "work"):
        _source(service, cid).fail_with = EventNotFoundError("gone")
    initialize(client)

    result = call(client, "list_events")

    assert result["isError"] is True
    assert "event_not_found" in result["content"][0]["text"]


def test_daily_brief_without_calendar_id_covers_every_calendar(client: TestClient, service: CalendarService) -> None:
    midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    _event(_source(service, "home"), "a", "Home thing", midnight + timedelta(hours=9), hours=0.5)
    _event(_source(service, "work"), "b", "Work thing", midnight + timedelta(hours=10), hours=0.5)
    initialize(client)

    brief = payload(call(client, "daily_brief", timezone="UTC"))

    assert brief["calendar_id"] == "all"
    assert {(e["title"], e["calendar_name"]) for e in brief["events"]} == {
        ("Home thing", "Home"),
        ("Work thing", "Work"),
    }
    assert brief["summary"]["total_events"] == 2
    assert [c["id"] for c in brief["calendars"]] == ["home", "work"]
    assert brief["errors"] == []


def test_instructions_say_reads_need_no_calendar_id(client: TestClient) -> None:
    resp = rpc(
        client,
        "initialize",
        {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}},
    )
    assert "without a calendar_id" in resp.json()["result"]["instructions"]
    tools = {t["name"]: t for t in rpc(client, "tools/list").json()["result"]["tools"]}
    assert "calendar_id" not in tools["list_events"]["inputSchema"].get("required", [])
    assert "calendar_id" not in tools["daily_brief"]["inputSchema"].get("required", [])
