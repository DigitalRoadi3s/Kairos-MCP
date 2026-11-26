"""OAuth discovery documents for /mcp.

Protected Resource Metadata (RFC 9728) tells a client like claude.ai which
authorization server issues tokens for /mcp. Kairos serves it itself, rather
than leaving it to the MCP SDK, so the issuer appears exactly as the issuer
names itself (the SDK appends a trailing slash, which strict clients reject).

For WorkOS AuthKit, Kairos also serves the issuer's own authorization server
metadata at its root, as WorkOS recommends for clients that skip RFC 9728.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException

from kairos.settings import Settings

METADATA_TTL_SECONDS = 3600.0


def build_router(settings: Settings, http: httpx.AsyncClient | None = None) -> APIRouter:
    router = APIRouter(include_in_schema=False)
    oidc = settings.oidc
    if oidc is None or settings.mcp_resource_url is None:
        return router

    resource: dict[str, Any] = {
        "resource": settings.mcp_resource_url,
        "authorization_servers": [oidc.issuer],
        "bearer_methods_supported": ["header"],
        "resource_name": "Kairos calendars",
    }
    if oidc.required_scopes:
        resource["scopes_supported"] = list(oidc.required_scopes)

    @router.get("/.well-known/oauth-protected-resource")
    @router.get("/.well-known/oauth-protected-resource/mcp")
    async def protected_resource() -> dict[str, Any]:
        return resource

    if oidc.provider != "workos":
        return router

    cache: dict[str, Any] = {"at": 0.0, "body": None}
    upstream = f"{oidc.issuer.rstrip('/')}/.well-known/oauth-authorization-server"

    @router.get("/.well-known/oauth-authorization-server")
    async def authorization_server() -> dict[str, Any]:
        if cache["body"] is not None and time.monotonic() - cache["at"] < METADATA_TTL_SECONDS:
            return cache["body"]
        client = http or httpx.AsyncClient(timeout=10)
        try:
            resp = await client.get(upstream)
            resp.raise_for_status()
            body = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            if cache["body"] is not None:
                return cache["body"]
            raise HTTPException(502, "Could not fetch the authorization server's metadata") from exc
        finally:
            if http is None:
                await client.aclose()
        cache.update(at=time.monotonic(), body=body)
        return body

    return router
