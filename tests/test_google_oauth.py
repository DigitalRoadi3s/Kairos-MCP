"""Google OAuth2 refresh-token handling."""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from kairos.sources.google_oauth import GoogleOAuth2Auth, GoogleOAuthError


def response(status: int = 200, token: str = "tok", expires_in: int = 3600, text: str = "{}") -> httpx.Response:
    if status == 200:
        return httpx.Response(status, json={"access_token": token, "expires_in": expires_in})
    return httpx.Response(status, text=text)


def request() -> SimpleNamespace:
    return SimpleNamespace(headers={})


TARGET = "kairos.sources.google_oauth.httpx.post"


def test_first_call_fetches_token() -> None:
    auth = GoogleOAuth2Auth("cid", "secret", "refresh")
    with patch(TARGET, return_value=response(token="tok-1")) as post:
        req = request()
        auth(req)
    assert req.headers["Authorization"] == "Bearer tok-1"
    post.assert_called_once()
    assert post.call_args.kwargs["data"]["grant_type"] == "refresh_token"


def test_token_reused_until_expiry() -> None:
    auth = GoogleOAuth2Auth("cid", "secret", "refresh")
    with patch(TARGET, return_value=response(token="tok-1")) as post:
        auth(request())
        auth(request())
    assert post.call_count == 1


def test_expired_token_refreshed() -> None:
    auth = GoogleOAuth2Auth("cid", "secret", "refresh")
    with patch(TARGET, return_value=response(token="tok-1")):
        auth(request())
    auth._expires_at = time.monotonic() - 1
    with patch(TARGET, return_value=response(token="tok-2")):
        req = request()
        auth(req)
    assert req.headers["Authorization"] == "Bearer tok-2"


def test_revoked_refresh_token_is_permanent() -> None:
    auth = GoogleOAuth2Auth("cid", "secret", "bad")
    with (
        patch(TARGET, return_value=response(400, text='{"error":"invalid_grant"}')),
        pytest.raises(GoogleOAuthError) as info,
    ):
        auth(request())
    assert not info.value.transient
    assert "invalid_grant" in str(info.value)


def test_google_outage_is_transient() -> None:
    auth = GoogleOAuth2Auth("cid", "secret", "refresh")
    with patch(TARGET, return_value=response(503)), pytest.raises(GoogleOAuthError) as info:
        auth(request())
    assert info.value.transient


def test_network_failure_is_transient() -> None:
    auth = GoogleOAuth2Auth("cid", "secret", "refresh")
    with patch(TARGET, side_effect=httpx.ConnectError("no route")), pytest.raises(GoogleOAuthError) as info:
        auth(request())
    assert info.value.transient
