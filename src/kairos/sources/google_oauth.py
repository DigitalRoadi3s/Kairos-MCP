"""OAuth 2.0 refresh-token auth for Google's CalDAV endpoint.

Google stopped accepting passwords for CalDAV in March 2025. The one-time
browser consent happens outside Kairos (see the README); with the refresh
token in hand, this class swaps it for short-lived access tokens and
renews them before they expire.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import httpx
from caldav.lib.http_sync import AuthBase

TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - a URL, not a secret
EXPIRY_SAFETY_MARGIN_SECONDS = 60


class GoogleOAuthError(Exception):
    """The token endpoint refused us or couldn't be reached.

    transient=False means the refresh token or client credentials were
    rejected and the operator has to redo the consent step.
    """

    def __init__(self, message: str, *, transient: bool) -> None:
        super().__init__(message)
        self.transient = transient


class GoogleOAuth2Auth(AuthBase):
    """Attaches a valid Bearer token to every CalDAV request. Thread-safe."""

    def __init__(self, client_id: str, client_secret: str, refresh_token: str, timeout: float = 30) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._timeout = timeout
        self._lock = threading.Lock()
        self._access_token: str | None = None
        self._expires_at = 0.0

    def __call__(self, request: Any) -> Any:
        request.headers["Authorization"] = f"Bearer {self._get_token()}"
        return request

    def _get_token(self) -> str:
        with self._lock:
            if self._access_token is None or time.monotonic() >= self._expires_at:
                self._refresh()
            assert self._access_token is not None
            return self._access_token

    def _refresh(self) -> None:
        try:
            resp = httpx.post(
                TOKEN_URL,
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "refresh_token": self._refresh_token,
                    "grant_type": "refresh_token",
                },
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise GoogleOAuthError(f"Could not reach Google's token endpoint: {exc}", transient=True) from exc

        if resp.status_code >= 500:
            raise GoogleOAuthError(f"Google's token endpoint failed (HTTP {resp.status_code})", transient=True)
        if resp.status_code != 200:
            raise GoogleOAuthError(
                f"Google rejected the refresh token (HTTP {resp.status_code}): {resp.text[:300]}. "
                "Redo the one-time authorization step in the README.",
                transient=False,
            )
        data = resp.json()
        self._access_token = data["access_token"]
        self._expires_at = time.monotonic() + float(data.get("expires_in", 3600)) - EXPIRY_SAFETY_MARGIN_SECONDS
