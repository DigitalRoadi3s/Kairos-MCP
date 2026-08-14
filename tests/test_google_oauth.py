"""
Tests for Google OAuth2 token handling (task 24).
"""
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from google_oauth import GoogleOAuth2Auth, GoogleOAuthError  # noqa: E402


def _fake_response(status_code=200, access_token="tok", expires_in=3600, text="{}"):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = {"access_token": access_token, "expires_in": expires_in}
    resp.text = text
    return resp


class TestGoogleOAuth2Auth:
    def test_first_call_fetches_token(self):
        auth = GoogleOAuth2Auth("cid", "csecret", "rtoken")
        with patch("google_oauth.requests.post", return_value=_fake_response(access_token="tok-1")) as mock_post:
            request = MagicMock(headers={})
            auth(request)
            assert request.headers["Authorization"] == "Bearer tok-1"
            mock_post.assert_called_once()

    def test_second_call_within_ttl_reuses_cached_token(self):
        auth = GoogleOAuth2Auth("cid", "csecret", "rtoken")
        with patch("google_oauth.requests.post", return_value=_fake_response(access_token="tok-1")) as mock_post:
            auth(MagicMock(headers={}))
            auth(MagicMock(headers={}))
            assert mock_post.call_count == 1, "should not re-fetch while token is still valid"

    def test_expired_token_triggers_refresh(self):
        auth = GoogleOAuth2Auth("cid", "csecret", "rtoken")
        with patch("google_oauth.requests.post", return_value=_fake_response(access_token="tok-1")):
            auth(MagicMock(headers={}))
        auth._expires_at = time.monotonic() - 1  # force expiry
        with patch("google_oauth.requests.post", return_value=_fake_response(access_token="tok-2")) as mock_post:
            request = MagicMock(headers={})
            auth(request)
            assert request.headers["Authorization"] == "Bearer tok-2"
            mock_post.assert_called_once()

    def test_revoked_refresh_token_raises_clear_error(self):
        auth = GoogleOAuth2Auth("cid", "csecret", "bad-token")
        with patch("google_oauth.requests.post",
                   return_value=_fake_response(status_code=400, text='{"error":"invalid_grant"}')):
            with pytest.raises(GoogleOAuthError):
                auth(MagicMock(headers={}))

    def test_network_failure_raises_clear_error(self):
        import requests
        auth = GoogleOAuth2Auth("cid", "csecret", "rtoken")
        with patch("google_oauth.requests.post", side_effect=requests.exceptions.ConnectionError("no route")):
            with pytest.raises(GoogleOAuthError):
                auth(MagicMock(headers={}))
