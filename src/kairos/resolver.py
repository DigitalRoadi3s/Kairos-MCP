"""Which CalendarService answers a request.

Self-hosted Kairos has one service for everyone. A multi-user host passes
create_app a resolver instead: an async function that takes the caller's
verified access token and returns that caller's own service. Kairos calls
it once per REST request and once per MCP tool call, after authentication,
so one caller can never reach another's calendars through a shared service.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import TYPE_CHECKING

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken

if TYPE_CHECKING:
    from kairos.service import CalendarService

ServiceResolver = Callable[[AccessToken | None], Awaitable["CalendarService"]]

# Set by the static-token gate on /mcp; the MCP SDK keeps its own for OIDC tokens.
_static_token: ContextVar[AccessToken | None] = ContextVar("kairos_static_token", default=None)


def static_resolver(service: CalendarService) -> ServiceResolver:
    """A resolver that answers every caller with the same service."""

    async def resolve(_token: AccessToken | None) -> CalendarService:
        return service

    return resolve


def current_token() -> AccessToken | None:
    """The verified token of the MCP request being handled, if any."""
    return get_access_token() or _static_token.get()


def set_static_token(token: AccessToken | None) -> object:
    return _static_token.set(token)


def reset_static_token(marker: object) -> None:
    _static_token.reset(marker)  # type: ignore[arg-type]
