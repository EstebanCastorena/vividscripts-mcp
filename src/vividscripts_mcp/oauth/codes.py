"""Authorization request state + authorization codes.

Two short-lived single-use stores feed the OAuth code grant:

- ``AuthRequestState`` — captured when ``/oauth/authorize`` validates a
  request, consumed by the mock IdP callback once the user "logs in".
  Holds the PKCE challenge, ``redirect_uri``, and ``state`` nonce so the
  callback can reconstruct the redirect to the client.
- ``AuthCode`` — issued by the mock IdP after login, redeemed once at
  ``/oauth/token`` (KAN-51). Carries the PKCE challenge so the token
  endpoint can verify the ``code_verifier``, plus the bound
  ``redirect_uri`` and ``client_id`` for RFC 6749 § 4.1.3 enforcement.

Both stores are single-use: ``consume()`` removes the entry whether or
not it has expired, so a leaked entry can't be replayed.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

# RFC 6749 § 4.1.2 recommends short-lived auth codes. 10 minutes matches the
# spec's "in the order of 10 minutes" guidance and the KAN-50 acceptance.
AUTH_CODE_TTL_SECONDS = 600
AUTH_REQUEST_TTL_SECONDS = 600


class AuthRequestState(BaseModel):
    """A validated ``/oauth/authorize`` request awaiting user authentication."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str
    client_id: str
    redirect_uri: str
    state: str | None
    code_challenge: str
    code_challenge_method: str
    scope: str | None
    expires_at: int  # Unix timestamp, seconds


class AuthCode(BaseModel):
    """An authorization code issued after user authentication.

    In the Cognito broker (KAN-85 / KAN-36 pass-through) the one-shot
    code also carries the Cognito tokens captured at ``/oauth/callback``,
    so ``/oauth/token`` can return them instead of self-minting. They
    stay ``None`` on the offline mock-IdP path (self-mint).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    client_id: str
    redirect_uri: str
    code_challenge: str
    code_challenge_method: str
    scope: str | None
    user_id: str
    expires_at: int
    cognito_access_token: str | None = None
    cognito_refresh_token: str | None = None
    cognito_expires_in: int | None = None


@runtime_checkable
class AuthRequestStateStore(Protocol):
    def add(self, state: AuthRequestState) -> None: ...

    def consume(self, request_id: str) -> AuthRequestState | None: ...


@runtime_checkable
class AuthCodeStore(Protocol):
    def add(self, code: AuthCode) -> None: ...

    def consume(self, code: str) -> AuthCode | None: ...


def _now() -> int:
    return int(datetime.now(UTC).timestamp())


class MockAuthRequestStateStore:
    """Thread-safe in-memory store for pending authorize requests."""

    def __init__(self) -> None:
        self._items: dict[str, AuthRequestState] = {}
        self._lock = threading.Lock()

    def add(self, state: AuthRequestState) -> None:
        with self._lock:
            self._items[state.request_id] = state

    def consume(self, request_id: str) -> AuthRequestState | None:
        with self._lock:
            entry = self._items.pop(request_id, None)
        if entry is None or entry.expires_at < _now():
            return None
        return entry


class MockAuthCodeStore:
    """Thread-safe in-memory store for issued authorization codes."""

    def __init__(self) -> None:
        self._codes: dict[str, AuthCode] = {}
        self._lock = threading.Lock()

    def add(self, code: AuthCode) -> None:
        with self._lock:
            self._codes[code.code] = code

    def consume(self, code: str) -> AuthCode | None:
        with self._lock:
            entry = self._codes.pop(code, None)
        if entry is None or entry.expires_at < _now():
            return None
        return entry
