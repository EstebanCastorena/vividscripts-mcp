"""Per-request authentication context.

After the Bearer middleware (KAN-52) validates an incoming token, it
binds the resulting :class:`UserClaims` to a :mod:`contextvars`
``ContextVar``. Tool handlers — which run in the same asyncio task as
the request that triggered them — read the current claims via
:func:`require_user_claims`.

Per-task isolation is a defense-in-depth assumption (ASGI servers
usually allocate a fresh task per request, which copies its parent
context), but the middleware does **not** rely on it for correctness.
:func:`set_user_claims` returns a ``contextvars.Token`` that the
middleware passes back to :func:`reset_user_claims` in a ``try/finally``
around the downstream call, so the bind is unwound on every code path
— success, early return, or downstream exception. The pair makes the
context strictly request-scoped regardless of how the server schedules
tasks (KAN-94, audit finding #1).
"""

from __future__ import annotations

import contextvars

from vividscripts_mcp.oauth.bearer import UserClaims

_current_claims: contextvars.ContextVar[UserClaims | None] = contextvars.ContextVar(
    "vividscripts_mcp_user_claims", default=None
)


def set_user_claims(claims: UserClaims | None) -> contextvars.Token[UserClaims | None]:
    """Bind the current request's authenticated user. Middleware-internal.

    Returns the ``contextvars.Token`` for the bind so the caller can pass
    it back to :func:`reset_user_claims` (typically in a ``try/finally``).
    Without that reset the bind persists in the caller's context and the
    next code path that reads :func:`get_user_claims` in the same task
    sees a stale identity (KAN-94, audit finding #1).
    """
    return _current_claims.set(claims)


def reset_user_claims(token: contextvars.Token[UserClaims | None]) -> None:
    """Restore the auth-context to its prior value. Middleware-internal."""
    _current_claims.reset(token)


def get_user_claims() -> UserClaims | None:
    """Return the current user's claims, or ``None`` if unauthenticated."""
    return _current_claims.get()


class AuthRequired(Exception):
    """Raised when a protected tool runs without an authenticated context."""


def require_user_claims() -> UserClaims:
    """Return the current user's claims, raising :class:`AuthRequired` if absent."""
    claims = _current_claims.get()
    if claims is None:
        raise AuthRequired("this tool requires an authenticated Bearer context")
    return claims
