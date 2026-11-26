"""MCP over streamable HTTP: auth gate, tool calls, protected-resource metadata."""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from kairos.app import create_app
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
        assert body["authorization_servers"] == ["https://id.example.com/"]
        unauth = rpc(client, "initialize", headers={k: v for k, v in HEADERS.items() if k != "Authorization"})
        assert unauth.status_code == 401
        assert "resource_metadata=" in unauth.headers["www-authenticate"]
        # static tokens still work alongside OIDC
        initialize(client)
