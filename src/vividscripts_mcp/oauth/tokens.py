"""Self-mint token primitives — **offline / test mode only**.

These mint locally-signed RS256 JWTs and opaque refresh tokens stored
in :class:`MockRefreshTokenStore`. The issuer/audience are placeholders
aligned with the offline PRM document so the Bearer validator accepts
them without a network.

The production **broker** path (KAN-85 / KAN-36 Cognito-direct
pass-through) does **not** use any of this: ``/oauth/token`` returns the
Cognito tokens captured at ``/oauth/callback`` and the Bearer validator
checks Cognito's JWKS. ``server.build_app`` only wires the self-mint
path when no ``CognitoConfig`` is supplied — i.e. the offline unit
suite and local dev. Kept (not deleted) precisely so that suite still
runs without AWS/Cognito.
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
    """Persisted refresh token. Opaque value mapped to the user/client it grants.

    KAN-98 #19 — ``family_id`` groups every rotation of a single original
    grant. Replaying a consumed token within a family revokes all live
    members of that family (reuse detection).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    refresh_token: str
    client_id: str
    user_id: str
    scope: str | None
    expires_at: int
    family_id: str


@runtime_checkable
class RefreshTokenStore(Protocol):
    def add(self, record: RefreshTokenRecord) -> None: ...

    def consume(self, refresh_token: str) -> RefreshTokenRecord | None: ...


class MockRefreshTokenStore:
    """Thread-safe in-memory refresh-token persistence.

    KAN-98 #19 — implements reuse detection. When :meth:`consume` is
    called with a token that was already consumed (tombstoned), the
    entire token family is revoked rather than silently returning
    ``None``. The token endpoint translates a ``None`` return into
    ``invalid_grant``; revoking the family ensures a covertly-stolen
    token converts to a noisy boot-out as soon as either party uses it.
    """

    def __init__(self) -> None:
        self._items: dict[str, RefreshTokenRecord] = {}
        # token -> family_id mapping for previously-consumed tokens.
        # Lookup hit here is the reuse signal that triggers family
        # revocation.
        self._consumed_families: dict[str, str] = {}
        # family_id -> set of live token strings. Lets us revoke an
        # entire family in O(family size) on reuse detection.
        self._family_members: dict[str, set[str]] = {}
        self._lock = threading.Lock()

    def add(self, record: RefreshTokenRecord) -> None:
        with self._lock:
            self._items[record.refresh_token] = record
            self._family_members.setdefault(record.family_id, set()).add(record.refresh_token)

    def consume(self, refresh_token: str) -> RefreshTokenRecord | None:
        with self._lock:
            # Reuse detection: a replay of a tombstoned token burns the
            # entire family. The token endpoint still returns
            # ``invalid_grant``; the legitimate holder of the rotated
            # token will hit the same dead-family path on their next
            # refresh and be forced to re-authenticate.
            family_id = self._consumed_families.get(refresh_token)
            if family_id is not None:
                for member in list(self._family_members.get(family_id, set())):
                    self._items.pop(member, None)
                self._family_members.pop(family_id, None)
                return None
            entry = self._items.pop(refresh_token, None)
            if entry is None:
                return None
            if entry.expires_at < int(datetime.now(UTC).timestamp()):
                # Expired by clock — natural rejection, not a reuse
                # signal. Don't tombstone (would generate a DoS on
                # every legitimate expiry).
                self._family_members.get(entry.family_id, set()).discard(refresh_token)
                return None
            # Tombstone the token so a future replay is detectable.
            self._consumed_families[refresh_token] = entry.family_id
            self._family_members.get(entry.family_id, set()).discard(refresh_token)
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
        "token_use": "access",  # nosec B105 — Cognito-shape claim discriminator, not a credential
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
    family_id: str | None = None,
) -> tuple[str, RefreshTokenRecord]:
    """Generate an opaque refresh token + its persistence record.

    ``family_id`` groups rotation chains for KAN-98 #19 reuse detection.
    Pass the prior token's family on each rotation so the entire chain
    is burnt if any earlier member is replayed; omit it for a brand-new
    grant so a fresh family is allocated.
    """
    token = secrets.token_urlsafe(32)
    now = int(datetime.now(UTC).timestamp())
    record = RefreshTokenRecord(
        refresh_token=token,
        client_id=client_id,
        user_id=user_id,
        scope=scope,
        expires_at=now + ttl_seconds,
        family_id=family_id if family_id is not None else secrets.token_urlsafe(16),
    )
    return token, record
