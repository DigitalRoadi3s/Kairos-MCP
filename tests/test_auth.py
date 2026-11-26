"""Static API tokens and OIDC JWT validation."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import anyio
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from kairos.auth import OIDCVerifier, TokenVerifier
from kairos.settings import OIDCConfig

from .conftest import TOKEN, make_settings

ISSUER = "https://id.example.com/application/o/kairos"
AUD = "https://cal.example.com/mcp"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeJWKS:
    def get_signing_key_from_jwt(self, token: str) -> Any:
        return SimpleNamespace(key=KEY.public_key())


def oidc(**kw: Any) -> OIDCVerifier:
    config = OIDCConfig(issuer=ISSUER, audiences=(AUD,), required_scopes=kw.pop("scopes", ("calendar",)), **kw)
    verifier = OIDCVerifier(config)

    async def jwks() -> FakeJWKS:
        return FakeJWKS()

    verifier._jwks_client = jwks  # type: ignore[method-assign]
    return verifier


def token(key: Any = KEY, alg: str = "RS256", **claims: Any) -> str:
    base: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUD,
        "sub": "eric",
        "exp": int(time.time()) + 600,
        "scope": "calendar openid",
        "azp": "claude",
    }
    base.update(claims)
    base = {k: v for k, v in base.items() if v is not None}
    return jwt.encode(base, key, algorithm=alg)


def verify(v: OIDCVerifier | TokenVerifier, tok: str) -> Any:
    if isinstance(v, TokenVerifier):
        return anyio.run(v.verify_token, tok)
    return anyio.run(v.verify, tok)


class TestStatic:
    def test_accepts_configured_token(self) -> None:
        result = verify(TokenVerifier(make_settings()), TOKEN)
        assert result is not None and result.client_id == "api-token"

    @pytest.mark.parametrize("bad", ["", "x" * 40, TOKEN[:-1], TOKEN + "x"])
    def test_rejects_others(self, bad: str) -> None:
        assert verify(TokenVerifier(make_settings()), bad) is None

    def test_static_token_carries_required_scopes(self) -> None:
        settings = make_settings(oidc=OIDCConfig(issuer=ISSUER, audiences=(AUD,), required_scopes=("calendar",)))
        result = verify(TokenVerifier(settings, oidc()), TOKEN)
        assert result.scopes == ["calendar"]

    def test_unconfigured(self) -> None:
        assert not TokenVerifier(make_settings(api_tokens=frozenset())).configured


class TestOIDC:
    def test_valid_token(self) -> None:
        result = verify(oidc(), token())
        assert result is not None
        assert result.client_id == "claude"
        assert result.subject == "eric"
        assert "calendar" in result.scopes

    def test_scp_list_claim(self) -> None:
        assert verify(oidc(), token(scope=None, scp=["calendar"])) is not None

    @pytest.mark.parametrize(
        "claims",
        [
            {"iss": "https://evil.example.com"},
            {"aud": "https://other.example.com/mcp"},
            {"exp": int(time.time()) - 120},
            {"exp": None},
            {"scope": "openid"},
        ],
        ids=["issuer", "audience", "expired", "no-exp", "missing-scope"],
    )
    def test_rejected_claims(self, claims: dict[str, Any]) -> None:
        assert verify(oidc(), token(**claims)) is None

    def test_wrong_signature(self) -> None:
        assert verify(oidc(), token(key=OTHER_KEY)) is None

    def test_hs256_rejected(self) -> None:
        assert verify(oidc(), token(key="s" * 64, alg="HS256")) is None

    def test_allowed_subjects(self) -> None:
        v = oidc(allowed_subjects=("eric",))
        assert verify(v, token()) is not None
        assert verify(v, token(sub="mallory")) is None

    def test_garbage(self) -> None:
        assert verify(oidc(), "a.b.c") is None

    def test_combined_verifier_tries_jwt_after_static(self) -> None:
        settings = make_settings(oidc=OIDCConfig(issuer=ISSUER, audiences=(AUD,), required_scopes=("calendar",)))
        v = TokenVerifier(settings, oidc())
        assert verify(v, token()).subject == "eric"
        assert verify(v, "not-a-jwt") is None


def test_discovery_falls_back_to_oauth_metadata() -> None:
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(404)
        return httpx.Response(200, json={"issuer": ISSUER, "jwks_uri": ISSUER + "/jwks/"})

    async def go() -> str:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            v = OIDCVerifier(OIDCConfig(issuer=ISSUER, audiences=(AUD,)), http)
            return await v._discover_jwks_uri()

    assert anyio.run(go) == ISSUER + "/jwks/"


def test_workos_rejects_agent_tokens() -> None:
    v = oidc(scopes=(), provider="workos")
    assert verify(v, token(scope=None)) is not None
    assert verify(v, token(scope=None, sub_profile="ai_agent")) is None


def test_trailing_slash_issuer_matches() -> None:
    v = oidc()
    v.config = OIDCConfig(issuer=ISSUER + "/", audiences=(AUD,), required_scopes=("calendar",))
    assert verify(v, token()) is not None
