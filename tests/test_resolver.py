"""Multi-user hosting: a resolver gives each caller their own CalendarService."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from mcp.server.auth.provider import AccessToken

from kairos.app import create_app
from kairos.errors import CalendarNotFoundError
from kairos.service import CalendarService
from kairos.settings import SourceConfig

from .conftest import FakeSource, fast_breaker, make_settings
from .test_mcp import payload, rpc

ALICE = "a" * 40
BOB = "b" * 40
STRANGER = "s" * 40


def one_calendar(cid: str) -> CalendarService:
    src = SourceConfig(id=cid, name=cid.title(), url="https://dav.example", username="u", password="p")
    return CalendarService(make_settings(sources=(src,)), FakeSource, breaker_factory=fast_breaker)


@pytest.fixture
def client() -> Iterator[TestClient]:
    services = {ALICE: one_calendar("alice-home"), BOB: one_calendar("bob-work")}
    seen: list[str | None] = []

    async def resolver(token: AccessToken | None) -> CalendarService:
        seen.append(token.token if token else None)
        if token is None or token.token not in services:
            raise CalendarNotFoundError("No calendars connected")
        return services[token.token]

    settings = make_settings(sources=(), api_tokens=frozenset({ALICE, BOB, STRANGER}))
    with TestClient(create_app(settings, resolver=resolver)) as c:
        c.app.state.seen = seen  # type: ignore[attr-defined]
        yield c


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_rest_answers_from_each_callers_service(client: TestClient) -> None:
    alice = client.get("/api/v1/calendars", headers=auth(ALICE)).json()["calendars"]
    bob = client.get("/api/v1/calendars", headers=auth(BOB)).json()["calendars"]
    assert [c["id"] for c in alice] == ["alice-home"]
    assert [c["id"] for c in bob] == ["bob-work"]


def test_rest_cannot_reach_another_callers_calendar(client: TestClient) -> None:
    resp = client.get("/api/v1/calendars/bob-work/events", headers=auth(ALICE))
    assert resp.status_code == 404


def test_resolver_errors_map_to_http(client: TestClient) -> None:
    resp = client.get("/api/v1/calendars", headers=auth(STRANGER))
    assert resp.status_code == 404
    assert resp.json()["error"] == "calendar_not_found"


def test_rest_auth_runs_before_the_resolver(client: TestClient) -> None:
    assert client.get("/api/v1/calendars").status_code == 401
    assert client.get("/api/v1/calendars", headers=auth("x" * 40)).status_code == 401
    assert client.app.state.seen == []  # type: ignore[attr-defined]


def test_health_is_a_liveness_check(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy", "calendars": {}}


def mcp_call(client: TestClient, token: str, tool: str) -> dict[str, Any]:
    headers = {**auth(token), "Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    init = {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}
    assert rpc(client, "initialize", init, headers=headers).status_code == 200
    resp = rpc(client, "tools/call", {"name": tool, "arguments": {}}, headers=headers)
    assert resp.status_code == 200, resp.text
    result: dict[str, Any] = resp.json()["result"]
    return result


def test_mcp_answers_from_each_callers_service(client: TestClient) -> None:
    assert [c["id"] for c in payload(mcp_call(client, ALICE, "list_calendars"))["calendars"]] == ["alice-home"]
    assert [c["id"] for c in payload(mcp_call(client, BOB, "list_calendars"))["calendars"]] == ["bob-work"]


def test_mcp_resolver_error_is_a_tool_error(client: TestClient) -> None:
    result = mcp_call(client, STRANGER, "list_calendars")
    assert result["isError"] is True
    assert "calendar_not_found" in result["content"][0]["text"]


def test_service_and_resolver_are_exclusive() -> None:
    async def resolver(token: AccessToken | None) -> CalendarService:
        raise AssertionError

    with pytest.raises(ValueError, match="not both"):
        create_app(make_settings(), one_calendar("x"), resolver=resolver)
