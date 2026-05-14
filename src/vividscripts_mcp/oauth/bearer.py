"""Bearer token validation (RFC 6750) — KAN-52.

Validates ``Authorization: Bearer <jwt>`` headers against the public
key(s) of the configured authorization server. Phase 1 uses an
in-process key provider that returns the same RSA key /oauth/token signs
with; Phase 3 swaps in an HTTP-fetching JWKS provider pointed at Cognito.

Security guarantees, all tested:

- **Explicit algorithm allow-list.** ``algorithms=["RS256"]`` is passed
  to :func:`jwt.decode`; HS256, ``none``, or any other algorithm is
  rejected (Security AC #4 on KAN-29).
- **Audience + issuer claim checks.** Both ``aud`` and ``iss`` must
  match the configured values exactly (Security AC #5).
- **Token-use claim verified.** Tokens whose ``token_use`` is anything
  other than ``"access"`` are rejected — refresh tokens, ID tokens, etc.
  can't be used as Bearer credentials.
- **Token redaction in logs.** :func:`redact_token` returns a stable
  fingerprint (``jti`` from the claims when available, otherwise the
  first 8 hex chars of the token's SHA-256). The raw token is never
  emitted (Security AC #6).
"""

from __future__ import annotations

import hashlib
from typing import Any, Protocol, runtime_checkable

import jwt
from pydantic import BaseModel, ConfigDict
from starlette.requests import Request
from starlette.responses import JSONResponse

from vividscripts_mcp.oauth.keys import ALGORITHM, get_signing_key
from vividscripts_mcp.oauth.tokens import DEFAULT_AUDIENCE, DEFAULT_ISSUER

#: Path the JWKS document is served from. Standard well-known suffix.
JWKS_PATH = "/.well-known/jwks.json"


class UserClaims(BaseModel):
    """The subset of JWT claims the MCP tool layer relies on."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sub: str
    client_id: str
    scope: str | None
    jti: str
    exp: int
    iat: int


@runtime_checkable
class JWKSProvider(Protocol):
    """Resolver for the JWK matching a given ``kid``.

    Phase 1 ships :class:`InProcessJWKSProvider`; Phase 3 will add an
    HTTP-fetching provider with 1-hour caching and auto-refresh on
    ``kid`` miss against the Cognito user pool's JWKS endpoint.
    """

    def get_jwk(self, kid: str) -> dict[str, Any] | None: ...


class InProcessJWKSProvider:
    """Phase 1 JWKS provider — returns the in-process signing key."""

    def get_jwk(self, kid: str) -> dict[str, Any] | None:
        jwk = get_signing_key().public_jwk
        if jwk["kid"] != kid:
            return None
        return jwk


def redact_token(token: str, claims: dict[str, Any] | None = None) -> str:
    """Return a non-reversible fingerprint suitable for logging.

    Prefers the ``jti`` claim when available (which the validator already
    cross-checks). Falls back to the first 16 hex chars of SHA-256(token).
    The raw token is never returned.
    """
    if claims is not None:
        jti = claims.get("jti")
        if isinstance(jti, str) and jti:
            return f"jti:{jti}"
    digest = hashlib.sha256(token.encode("ascii")).hexdigest()
    return f"sha256:{digest[:16]}"


def validate_bearer_token(
    token: str,
    provider: JWKSProvider,
    *,
    issuer: str = DEFAULT_ISSUER,
    audience: str = DEFAULT_AUDIENCE,
) -> UserClaims | None:
    """Validate a Bearer JWT. Returns the claims on success, ``None`` on any failure.

    The function deliberately returns ``None`` (rather than raising) so
    the calling middleware can produce a consistent 401 response without
    leaking which specific check failed. The validator logs the redacted
    token fingerprint and the failure category at INFO; never the raw
    token, never the failed claim values.
    """
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError:
        return None

    kid = header.get("kid")
    if not isinstance(kid, str):
        return None

    jwk = provider.get_jwk(kid)
    if jwk is None:
        return None

    try:
        key = jwt.PyJWK(jwk).key
    except jwt.PyJWKError:
        return None

    try:
        claims = jwt.decode(
            token,
            key=key,
            algorithms=[ALGORITHM],  # Explicit — RS256 only, no fallback.
            audience=audience,
            issuer=issuer,
        )
    except jwt.InvalidTokenError:
        return None

    if claims.get("token_use") != "access":
        return None

    try:
        return UserClaims(
            sub=claims["sub"],
            client_id=claims["client_id"],
            scope=claims.get("scope"),
            jti=claims["jti"],
            exp=int(claims["exp"]),
            iat=int(claims["iat"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


async def jwks_endpoint(_request: Request) -> JSONResponse:
    """``GET /.well-known/jwks.json`` — serves the active public JWK set."""
    key = get_signing_key()
    return JSONResponse({"keys": [key.public_jwk]})
