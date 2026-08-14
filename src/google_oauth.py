"""
Google Calendar OAuth2 support (task 24).

Google disabled username/password CalDAV auth for all accounts on
March 14, 2025 - OAuth 2.0 is now required. This module implements the
refresh-token flow: the *authorization* step (the one-time browser
consent screen) is NOT something a headless backend service can do
itself, so it's a one-time setup step the operator runs externally
(see README "Connecting Google Calendar"). Once you have a refresh
token, this module handles everything else automatically: exchanging
it for short-lived access tokens and refreshing them as they expire,
with no further user interaction.

Interface for config.py (task 2/17): pass a GoogleOAuth2Auth instance
as the `auth=` kwarg to caldav.DAVClient instead of username/password.
"""
from __future__ import annotations

import threading
import time

import requests

TOKEN_URL = "https://oauth2.googleapis.com/token"
# Refresh a bit before actual expiry to avoid a request landing exactly
# as the token dies mid-flight.
EXPIRY_SAFETY_MARGIN_SECONDS = 60


class GoogleOAuthError(Exception):
    """Raised when the token endpoint rejects the refresh token or
    credentials - almost always means the refresh token was revoked
    and the operator needs to redo the one-time authorization step."""


class GoogleOAuth2Auth(requests.auth.AuthBase):
    """
    A requests-compatible auth object that attaches a valid Bearer
    token to every request, transparently refreshing it as needed.
    Pass an instance of this as caldav.DAVClient(auth=...).

    Thread-safe: multiple requests on the same CalDAVSourceClient can
    trigger a refresh concurrently without duplicate token exchanges
    (all but the first block on the lock and reuse the token the first
    one fetched).
    """

    def __init__(self, client_id: str, client_secret: str, refresh_token: str):
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._lock = threading.Lock()
        self._access_token: str | None = None
        self._expires_at: float = 0.0

    def __call__(self, request):
        request.headers["Authorization"] = f"Bearer {self._get_token()}"
        return request

    def _get_token(self) -> str:
        with self._lock:
            if self._access_token is None or time.monotonic() >= self._expires_at:
                self._refresh()
            return self._access_token

    def _refresh(self) -> None:
        try:
            resp = requests.post(TOKEN_URL, data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "refresh_token": self._refresh_token,
                "grant_type": "refresh_token",
            }, timeout=30)
        except requests.exceptions.RequestException as exc:
            raise GoogleOAuthError(f"Could not reach Google's token endpoint: {exc}") from exc

        if resp.status_code != 200:
            raise GoogleOAuthError(
                f"Google rejected the refresh token (HTTP {resp.status_code}): "
                f"{resp.text[:300]}. The refresh token may have been revoked - "
                "redo the one-time authorization step (see README)."
            )

        data = resp.json()
        self._access_token = data["access_token"]
        # expires_in is seconds from now, per Google's token response spec.
        self._expires_at = time.monotonic() + data["expires_in"] - EXPIRY_SAFETY_MARGIN_SECONDS
