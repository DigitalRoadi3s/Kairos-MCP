"""Inbound authentication for both adapters.

Two kinds of bearer token are accepted:

* Static API tokens (KAIROS_API_TOKENS) for your own scripts and agents.
* JWT access tokens from an external OAuth 2.0 / OIDC authorization server
  (KAIROS_OIDC_ISSUER), which is what claude.ai custom connectors use. Kairos
  acts only as a resource server: it publishes Protected Resource Metadata
  (RFC 9728) pointing at your issuer and validates the tokens it issues.
"""

from __future__ import annotations

import hmac
import logging
import time
from typing import Any

import anyio.to_thread
import httpx
import jwt
from mcp.server.auth.provider import AccessToken

from kairos.settings import OIDCConfig, Settings

logger = logging.getLogger("kairos.auth")

ALLOWED_ALGORITHMS = ["RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "EdDSA"]
DISCOVERY_TTL_SECONDS = 3600.0


class OIDCVerifier:
    """Validates JWT access tokens against an issuer's published signing keys."""

    def __init__(self, config: OIDCConfig, http: httpx.AsyncClient | None = None) -> None:
        self.config = config
        self._http = http
        self._jwks: jwt.PyJWKClient | None = None
        self._jwks_loaded_at = 0.0

    async def _jwks_client(self) -> jwt.PyJWKClient:
        if self._jwks is not None and time.monotonic() - self._jwks_loaded_at < DISCOVERY_TTL_SECONDS:
            return self._jwks
        jwks_uri = self.config.jwks_uri or await self._discover_jwks_uri()
        self._jwks = jwt.PyJWKClient(jwks_uri, cache_keys=True, lifespan=int(DISCOVERY_TTL_SECONDS), timeout=10)
        self._jwks_loaded_at = time.monotonic()
        return self._jwks

    async def _discover_jwks_uri(self) -> str:
        issuer = self.config.issuer
        candidates = [
            f"{issuer}/.well-known/openid-configuration",
            f"{issuer}/.well-known/oauth-authorization-server",
        ]
        client = self._http or httpx.AsyncClient(timeout=10)
        try:
            for url in candidates:
                try:
                    resp = await client.get(url)
                except httpx.HTTPError:
                    continue
                if resp.status_code == 200 and "jwks_uri" in resp.json():
                    return str(resp.json()["jwks_uri"])
        finally:
            if self._http is None:
                await client.aclose()
        raise RuntimeError(f"Could not discover jwks_uri for issuer {issuer}")

    async def verify(self, token: str) -> AccessToken | None:
        try:
            jwks = await self._jwks_client()
            key = await anyio.to_thread.run_sync(jwks.get_signing_key_from_jwt, token)
            claims: dict[str, Any] = jwt.decode(
                token,
                key.key,
                algorithms=ALLOWED_ALGORITHMS,
                audience=list(self.config.audiences),
                options={"require": ["exp", "iss", "aud"]},
                leeway=30,
            )
        except (jwt.PyJWTError, RuntimeError, httpx.HTTPError) as exc:
            logger.info("oidc_token_rejected", extra={"reason": type(exc).__name__})
            return None

        if str(claims.get("iss", "")).rstrip("/") != self.config.issuer:
            logger.info("oidc_token_rejected", extra={"reason": "issuer_mismatch"})
            return None
        subject = str(claims.get("sub", ""))
        if self.config.allowed_subjects and subject not in self.config.allowed_subjects:
            logger.info("oidc_token_rejected", extra={"reason": "subject_not_allowed"})
            return None
        scopes = _scopes(claims)
        if not set(self.config.required_scopes) <= set(scopes):
            logger.info("oidc_token_rejected", extra={"reason": "missing_scope"})
            return None

        aud = claims["aud"]
        audiences = [aud] if isinstance(aud, str) else list(aud)
        resource = next((a for a in audiences if a in self.config.audiences), None)
        return AccessToken(
            token=token,
            client_id=str(claims.get("azp") or claims.get("client_id") or subject or "unknown"),
            scopes=scopes,
            expires_at=int(claims["exp"]),
            resource=resource,
            subject=subject or None,
            claims={"iss": claims["iss"]},
        )


def _scopes(claims: dict[str, Any]) -> list[str]:
    raw = claims.get("scope", claims.get("scp", []))
    if isinstance(raw, str):
        return raw.split()
    return [str(s) for s in raw]


class TokenVerifier:
    """The single verifier used by REST and by /mcp (it satisfies the MCP SDK's TokenVerifier protocol)."""

    def __init__(self, settings: Settings, oidc: OIDCVerifier | None = None) -> None:
        self._tokens = [t.encode() for t in settings.api_tokens]
        # Static tokens carry the scopes /mcp requires, so they pass the SDK's scope check.
        self._static_scopes = list(settings.oidc.required_scopes) if settings.oidc else []
        self._oidc = oidc or (OIDCVerifier(settings.oidc) if settings.oidc else None)

    @property
    def configured(self) -> bool:
        return bool(self._tokens) or self._oidc is not None

    def _static(self, token: str) -> bool:
        candidate = token.encode()
        # Compare against every token so timing doesn't reveal which one matched.
        return any([hmac.compare_digest(candidate, t) for t in self._tokens])

    async def verify_token(self, token: str) -> AccessToken | None:
        if not token:
            return None
        if self._static(token):
            return AccessToken(token=token, client_id="api-token", scopes=self._static_scopes, subject="api-token")
        if self._oidc is not None and token.count(".") == 2:
            return await self._oidc.verify(token)
        return None
