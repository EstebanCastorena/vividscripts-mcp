"""Per-request authentication context.

After the Bearer middleware (KAN-52) validates an incoming token, it
binds the resulting :class:`UserClaims` to a :mod:`contextvars`
``ContextVar``. Tool handlers — which run in the same asyncio task as
the request that triggered them — read the current claims via
:func:`require_user_claims`.

``contextvars`` are task-local: each ASGI request runs in its own task
(directly or via ``asyncio.create_task``, which copies the parent
context). So even though the var is module-level, concurrent requests
don't see each other's claims. This is the same idiom Starlette uses
internally for request-scoped state.
"""

from __future__ import annotations

import contextvars

from vividscripts_mcp.oauth.bearer import UserClaims

_current_claims: contextvars.ContextVar[UserClaims | None] = contextvars.ContextVar(
    "vividscripts_mcp_user_claims", default=None
)


def set_user_claims(claims: UserClaims | None) -> None:
    """Bind the current request's authenticated user. Middleware-internal."""
    _current_claims.set(claims)


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
