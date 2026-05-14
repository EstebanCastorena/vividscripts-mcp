"""Access and refresh token primitives for ``/oauth/token``.

Phase 1 mints locally-signed RS256 JWTs as access tokens and opaque
random strings as refresh tokens, stored in :class:`MockRefreshTokenStore`.

The issuer and audience are Phase 1 placeholders shared with KAN-48's
PRM document — so a Bearer validator built against the PRM's
``authorization_servers`` claim accepts these tokens. Phase 3 replaces
both the signing path (Cognito mints) and the refresh store (Cognito
manages refresh tokens server-side).
"""

from __future__ import annotations

import secrets
import threading
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

import jwt
from pydantic import BaseModel, ConfigDict

from vividscripts_mcp.oauth.keys import ALGORITHM, KID, get_signing_key

#: Access token TTL. One hour matches typical OAuth deployments and gives
#: Claude Code a refresh cadence comparable to Cognito's default.
ACCESS_TOKEN_TTL_SECONDS = 3600

#: Refresh token TTL. 30 days; Phase 3 will inherit Cognito's value.
REFRESH_TOKEN_TTL_SECONDS = 30 * 24 * 3600

#: Phase 1 placeholders, aligned with ``oauth.metadata``. The Bearer
#: validator (KAN-52) checks tokens against these exact values.
DEFAULT_ISSUER = "https://app.vividscripts.com"
DEFAULT_AUDIENCE = "https://app.vividscripts.com/mcp"


class RefreshTokenRecord(BaseModel):
    """Persisted refresh token. Opaque value mapped to the user/client it grants."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    refresh_token: str
    client_id: str
    user_id: str
    scope: str | None
    expires_at: int


@runtime_checkable
class RefreshTokenStore(Protocol):
    def add(self, record: RefreshTokenRecord) -> None: ...

    def consume(self, refresh_token: str) -> RefreshTokenRecord | None: ...


class MockRefreshTokenStore:
    """Thread-safe in-memory refresh-token persistence."""

    def __init__(self) -> None:
        self._items: dict[str, RefreshTokenRecord] = {}
        self._lock = threading.Lock()

    def add(self, record: RefreshTokenRecord) -> None:
        with self._lock:
            self._items[record.refresh_token] = record

    def consume(self, refresh_token: str) -> RefreshTokenRecord | None:
        with self._lock:
            entry = self._items.pop(refresh_token, None)
        if entry is None or entry.expires_at < int(datetime.now(UTC).timestamp()):
            return None
        return entry


def mint_access_token(
    *,
    user_id: str,
    client_id: str,
    scope: str | None = None,
    issuer: str = DEFAULT_ISSUER,
    audience: str = DEFAULT_AUDIENCE,
    ttl_seconds: int = ACCESS_TOKEN_TTL_SECONDS,
) -> tuple[str, int]:
    """Mint an RS256-signed JWT access token. Returns (token, expires_in)."""
    now = int(datetime.now(UTC).timestamp())
    claims: dict[str, Any] = {
        "iss": issuer,
        "aud": audience,
        "sub": user_id,
        "client_id": client_id,
        "iat": now,
        "exp": now + ttl_seconds,
        "token_use": "access",
        "jti": secrets.token_urlsafe(12),
    }
    if scope is not None:
        claims["scope"] = scope

    key = get_signing_key()
    token = jwt.encode(
        claims,
        key.private_pem,
        algorithm=ALGORITHM,
        headers={"kid": KID},
    )
    return token, ttl_seconds


def mint_refresh_token(
    *,
    user_id: str,
    client_id: str,
    scope: str | None = None,
    ttl_seconds: int = REFRESH_TOKEN_TTL_SECONDS,
) -> tuple[str, RefreshTokenRecord]:
    """Generate an opaque refresh token + its persistence record."""
    token = secrets.token_urlsafe(32)
    now = int(datetime.now(UTC).timestamp())
    record = RefreshTokenRecord(
        refresh_token=token,
        client_id=client_id,
        user_id=user_id,
        scope=scope,
        expires_at=now + ttl_seconds,
    )
    return token, record
