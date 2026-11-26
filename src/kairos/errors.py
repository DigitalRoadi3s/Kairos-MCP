"""Error hierarchy shared by the core, the REST adapter and the MCP adapter.

Each error carries the HTTP status the REST layer returns for it, so the
mapping lives in one place instead of being repeated in every handler.
"""

from __future__ import annotations


class KairosError(Exception):
    status_code = 500
    code = "internal_error"


class InvalidRequestError(KairosError):
    """The caller sent something we can't act on (bad dates, bad RRULE...)."""

    status_code = 400
    code = "invalid_request"


class CalendarNotFoundError(KairosError):
    status_code = 404
    code = "calendar_not_found"


class EventNotFoundError(KairosError):
    status_code = 404
    code = "event_not_found"


class ReadOnlyCalendarError(KairosError):
    """The source is configured with writable: false."""

    status_code = 403
    code = "calendar_read_only"


class EventLockedError(KairosError):
    """The event starts inside the lock window, or a recurring event's time was changed."""

    status_code = 409
    code = "event_locked"


class ConflictError(KairosError):
    """Someone else changed the event between our read and our write (ETag mismatch)."""

    status_code = 409
    code = "conflict"


class UpstreamAuthError(KairosError):
    """The CalDAV server rejected our credentials. Not the caller's fault."""

    status_code = 502
    code = "upstream_auth_failed"


class SourceUnavailableError(KairosError):
    """Timeout, connection error, TLS failure, rate limit, or open circuit."""

    status_code = 503
    code = "source_unavailable"
