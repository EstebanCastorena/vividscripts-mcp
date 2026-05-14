"""Session storage for the OAuth surface.

Several OAuth endpoints (DCR, authorize) require the caller to already
be authenticated against the MCP server's host. Phase 1 represents that
with an opaque session cookie stored in a ``SessionStore``. KAN-50's
mock IdP issues sessions into the store after a "login"; KAN-49's DCR
endpoint reads from the store to enforce the prior-session gate.

Phase 3 replaces ``MockSessionStore`` with Cognito-backed session
validation: the cookie carries a Cognito ID token; ``get()`` validates
it via JWKS rather than dict lookup.
"""

from __future__ import annotations

import secrets
import threading
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict
from starlette.requests import Request

SESSION_COOKIE_NAME = "vs_session"


class SessionInfo(BaseModel):
    """A validated session: ``session_id`` belongs to ``user_id``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    user_id: str


@runtime_checkable
class SessionStore(Protocol):
    """Persistence contract for authenticated sessions."""

    def create(self, user_id: str) -> SessionInfo: ...

    def get(self, session_id: str) -> SessionInfo | None: ...

    def revoke(self, session_id: str) -> None: ...


class MockSessionStore:
    """Thread-safe in-memory SessionStore.

    Used in tests and the Phase 1 dev server. KAN-50's mock IdP calls
    ``create()`` after a successful login; the OAuth endpoints call
    ``get()`` to enforce auth gates.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, SessionInfo] = {}
        self._lock = threading.Lock()

    def create(self, user_id: str) -> SessionInfo:
        session_id = secrets.token_urlsafe(24)
        info = SessionInfo(session_id=session_id, user_id=user_id)
        with self._lock:
            self._sessions[session_id] = info
        return info

    def get(self, session_id: str) -> SessionInfo | None:
        with self._lock:
            return self._sessions.get(session_id)

    def revoke(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)


def require_session(request: Request, store: SessionStore) -> SessionInfo | None:
    """Extract + validate the session cookie. Return ``None`` if missing/invalid.

    The endpoint handler is responsible for translating ``None`` into a
    401 response (the helper deliberately doesn't raise, so handlers can
    customize the error payload per RFC).
    """
    sid = request.cookies.get(SESSION_COOKIE_NAME)
    if not sid:
        return None
    return store.get(sid)
