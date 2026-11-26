"""ASGI application: REST routes plus the MCP endpoint at /mcp, sharing one CalendarService."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from mcp.server.transport_security import TransportSecuritySettings
from starlette.types import ASGIApp, Receive, Scope, Send

from kairos import __version__
from kairos.api import rest
from kairos.api.mcp import build_mcp
from kairos.auth import TokenVerifier
from kairos.errors import KairosError
from kairos.observability import api_errors_total, api_request_duration_seconds, setup_logging
from kairos.service import CalendarService
from kairos.settings import Settings, load_settings

logger = logging.getLogger("kairos.app")


class StaticBearerGate:
    """Guards /mcp with the static API tokens when no OIDC issuer is configured."""

    def __init__(self, app: ASGIApp, verifier: TokenVerifier) -> None:
        self.app = app
        self.verifier = verifier

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"].startswith("/mcp"):
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            scheme, _, token = headers.get("authorization", "").partition(" ")
            ok = scheme.lower() == "bearer" and await self.verifier.verify_token(token.strip()) is not None
            if not ok:
                response = JSONResponse(
                    {"error": "invalid_token", "error_description": "Authentication required"},
                    status_code=401,
                    headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def _transport_security(settings: Settings) -> TransportSecuritySettings:
    if settings.allowed_hosts:
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(settings.allowed_hosts),
            allowed_origins=[f"https://{h}" for h in settings.allowed_hosts] + ["https://claude.ai"],
        )
    return TransportSecuritySettings(enable_dns_rebinding_protection=False)


def create_app(settings: Settings | None = None, service: CalendarService | None = None) -> FastAPI:
    settings = settings or load_settings()
    setup_logging(settings.log_level)
    service = service or CalendarService(settings)
    verifier = TokenVerifier(settings)

    if settings.insecure_no_auth:
        logger.warning("auth_disabled", extra={"note": "KAIROS_INSECURE_NO_AUTH is set; anyone can use the API"})
    elif not verifier.configured:
        logger.error(
            "auth_not_configured",
            extra={"note": "Set KAIROS_API_TOKENS or KAIROS_OIDC_ISSUER; every API call is refused until then"},
        )

    sdk_auth = settings.oidc is not None and not settings.insecure_no_auth
    mcp = build_mcp(service, settings, verifier if sdk_auth else None)
    mcp_app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=_transport_security(settings),
        host=settings.host,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await service.start()
        async with mcp.session_manager.run():
            yield
        await service.close()

    app = FastAPI(
        title="Kairos-MCP",
        version=__version__,
        description="CalDAV calendars as a REST API and an MCP server.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.service = service
    app.state.verifier = verifier

    @app.exception_handler(KairosError)
    async def kairos_error(_: Request, exc: KairosError) -> JSONResponse:
        return JSONResponse({"detail": str(exc), "error": exc.code}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"loc": e.get("loc"), "msg": e.get("msg")} for e in exc.errors()]
        return JSONResponse({"detail": errors, "error": "invalid_request"}, status_code=422)

    @app.middleware("http")
    async def metrics_middleware(request: Request, call_next: Callable[[Request], Awaitable[Any]]) -> Any:
        started = time.perf_counter()
        response = await call_next(request)
        route = request.scope.get("route")
        path = getattr(route, "path", None) or ("/mcp" if request.url.path.startswith("/mcp") else "unmatched")
        api_request_duration_seconds.labels(method=request.method, path=path).observe(time.perf_counter() - started)
        if response.status_code >= 400:
            api_errors_total.labels(status_code=str(response.status_code)).inc()
        return response

    app.include_router(rest.public)
    app.include_router(rest.router, dependencies=[Depends(rest.require_auth)])
    # The MCP sub-app owns /mcp and, with OIDC, /.well-known/oauth-protected-resource/mcp.
    app.mount("/", StaticBearerGate(mcp_app, verifier) if not (sdk_auth or settings.insecure_no_auth) else mcp_app)
    return app
