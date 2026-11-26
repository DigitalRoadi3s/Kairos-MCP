"""CalDAVSource error mapping. Real HTTP behaviour is covered by tests/integration."""

from __future__ import annotations

import pytest
from caldav.lib import error as dav_error

from kairos.errors import (
    ConflictError,
    EventNotFoundError,
    InvalidRequestError,
    SourceUnavailableError,
    UpstreamAuthError,
)
from kairos.settings import SourceConfig
from kairos.sources.caldav_source import CalDAVSource
from kairos.sources.google_oauth import GoogleOAuthError


def source(auth_type: str = "basic") -> CalDAVSource:
    return CalDAVSource(
        SourceConfig(
            id="home",
            name="Home",
            url="https://dav.example",
            username="u",
            password="p",
            auth_type=auth_type,
            calendar_path="/cal/",
        )
    )


@pytest.mark.parametrize(
    ("raised", "expected", "uid"),
    [
        (dav_error.AuthorizationError("401"), UpstreamAuthError, None),
        (dav_error.NotFoundError("404"), EventNotFoundError, "abc"),
        (dav_error.NotFoundError("404"), SourceUnavailableError, None),
        (dav_error.PutError("415"), InvalidRequestError, None),
        (dav_error.DAVError("500"), SourceUnavailableError, None),
        (ConnectionError("refused"), SourceUnavailableError, None),
        (TimeoutError("slow"), SourceUnavailableError, None),
        (GoogleOAuthError("down", transient=True), SourceUnavailableError, None),
        (GoogleOAuthError("revoked", transient=False), UpstreamAuthError, None),
    ],
)
def test_error_mapping(raised: Exception, expected: type[Exception], uid: str | None) -> None:
    with pytest.raises(expected), source()._errors(uid):
        raise raised


def test_etag_conflict() -> None:
    conflict = getattr(dav_error, "ETagMismatchError", None) or dav_error.PutError
    with pytest.raises((ConflictError, InvalidRequestError)), source()._errors("abc"):
        raise conflict("412")


def test_icloud_hint() -> None:
    with pytest.raises(UpstreamAuthError, match="app-specific password"), source()._errors():
        raise dav_error.AuthorizationError("401")


def test_unreachable_server_maps_to_unavailable() -> None:
    src = CalDAVSource(
        SourceConfig(
            id="x", name="x", url="http://127.0.0.1:9/", username="u", password="p", calendar_path="/cal/", timeout=2
        )
    )
    with pytest.raises(SourceUnavailableError):
        src.connect()
    assert not src.connected
