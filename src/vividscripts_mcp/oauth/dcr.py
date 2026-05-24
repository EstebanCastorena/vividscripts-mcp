"""Dynamic Client Registration endpoint (RFC 7591) — KAN-49.

Implements ``POST /oauth/register`` with these guarantees:

- Session-gated **in offline mode only**: rejects unauthenticated
  requests with 401 (mitigates KAN-46 / threat 1.4 — DCR replay
  attack). In the Cognito **broker** (KAN-85) registration is open per
  RFC 7591: the real authentication gate is Cognito Hosted UI at the
  authorize step, so a client that registers but never completes the
  Cognito login obtains no tokens. Gating DCR on a local session there
  would also be impossible — the broker has no local IdP to mint one,
  and it breaks the standard MCP DCR-then-authorize flow Claude Code
  performs. Residual DCR-spam risk is covered by the deferred
  rate-limiting follow-up (KAN-38).
- Redirect-URI safety: per RFC 8252 § 7, accepts only HTTPS or
  loopback-IP/localhost addresses. Public web URLs over plaintext HTTP
  are rejected to prevent token-leak attacks.
- Metadata allow-listing: validates ``grant_types``, ``response_types``,
  and ``token_endpoint_auth_method`` against an explicit set rather
  than echoing whatever the client sent.
- Audit-logged: every successful registration emits a structured event
  via :mod:`vividscripts_mcp.oauth.audit`.

Rate limiting is intentionally out of scope for KAN-49 (deferred to a
follow-up per KAN-38 decision). The endpoint structure leaves a clean
seam for middleware-based limits later.
"""

from __future__ import annotations

import secrets
import urllib.parse
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

from vividscripts_mcp.oauth.audit import emit_audit_event
from vividscripts_mcp.oauth.ratelimit import GlobalRateLimiter
from vividscripts_mcp.oauth.session import SessionStore, require_session
from vividscripts_mcp.oauth.store import ClientStore, RegisteredClient

# Allow-lists: what we accept from registration requests. Anything not in
# these sets is rejected with invalid_client_metadata rather than silently
# stored — the principle is that the server's allowed surface is the source
# of truth, not the client's request.
_ALLOWED_GRANT_TYPES = frozenset({"authorization_code", "refresh_token"})
_ALLOWED_RESPONSE_TYPES = frozenset({"code"})
# KAN-97 #7 — restricted to {"none"} to match what the AS metadata document
# advertises and what /oauth/token actually enforces. Any
# ``client_secret_*`` method would advertise confidential-client semantics
# the package does not implement (no secret is ever issued or verified).
_ALLOWED_AUTH_METHODS = frozenset({"none"})

# KAN-97 #8 — RFC 8252 §7 loopback host allow-list, exact-match against the
# parsed hostname (not a string prefix). ``localhost.attacker.com`` does
# not match ``localhost``; ``127.0.0.1.attacker.com`` does not match
# ``127.0.0.1``.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class RegistrationRequest(BaseModel):
    """RFC 7591 § 2.0 client metadata accepted by ``POST /oauth/register``.

    ``extra="allow"`` because RFC 7591 permits unrecognized metadata
    fields — the server stores what it knows and ignores the rest.
    """

    model_config = ConfigDict(extra="allow")

    redirect_uris: list[str] = Field(..., min_length=1)
    token_endpoint_auth_method: str = "none"
    grant_types: list[str] = Field(default_factory=lambda: ["authorization_code", "refresh_token"])
    response_types: list[str] = Field(default_factory=lambda: ["code"])
    client_name: str | None = None


def _is_safe_redirect_uri(uri: str) -> bool:
    """Per RFC 8252 § 7: HTTPS, or loopback over HTTP (native clients).

    Parses with :mod:`urllib.parse` and matches the hostname exactly
    against the loopback allow-list, so ``http://localhost.attacker.com``
    no longer slides through a naive ``startswith`` check (KAN-97 #8).
    Rejects URIs that carry embedded credentials (``user:pass@host``) or
    fragments — neither belongs in a registered ``redirect_uri``, and
    both have been used in the past to obscure the real netloc.
    """
    try:
        parsed = urllib.parse.urlsplit(uri)
    except ValueError:
        return False

    if parsed.scheme not in {"http", "https"}:
        return False
    if parsed.fragment:
        return False
    # ``username``/``password`` are populated only when ``@`` appears in
    # the netloc. Raw-``@`` URIs (no credential semantics) are also
    # rejected to avoid format-confusion oracles.
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        return False
    hostname = parsed.hostname
    if not hostname:
        return False

    if parsed.scheme == "https":
        return True
    # http: loopback-only.
    return hostname in _LOOPBACK_HOSTS


def _error_response(error: str, description: str, status_code: int) -> JSONResponse:
    """RFC 7591 § 3.2.2 / RFC 6749 § 5.2 standard error payload."""
    return JSONResponse(
        {"error": error, "error_description": description},
        status_code=status_code,
    )


def _validate_metadata(req: RegistrationRequest) -> JSONResponse | None:
    """Return an error response if any field is unacceptable, else None."""
    for uri in req.redirect_uris:
        if not _is_safe_redirect_uri(uri):
            return _error_response(
                "invalid_redirect_uri",
                f"redirect_uri must be HTTPS or loopback: {uri}",
                status_code=400,
            )

    if req.token_endpoint_auth_method not in _ALLOWED_AUTH_METHODS:
        return _error_response(
            "invalid_client_metadata",
            f"unsupported token_endpoint_auth_method: {req.token_endpoint_auth_method}",
            status_code=400,
        )

    invalid_grants = set(req.grant_types) - _ALLOWED_GRANT_TYPES
    if invalid_grants:
        return _error_response(
            "invalid_client_metadata",
            f"unsupported grant_types: {sorted(invalid_grants)}",
            status_code=400,
        )

    invalid_responses = set(req.response_types) - _ALLOWED_RESPONSE_TYPES
    if invalid_responses:
        return _error_response(
            "invalid_client_metadata",
            f"unsupported response_types: {sorted(invalid_responses)}",
            status_code=400,
        )

    return None


#: ``owner_user_id`` for clients registered through the open broker
#: path: there is no local session/user at registration time (the user
#: identity is established later at Cognito login and bound into the
#: auth code at ``/oauth/callback``).
BROKER_CLIENT_OWNER = "cognito-delegated"


def make_register_handler(
    client_store: ClientStore,
    session_store: SessionStore,
    *,
    session_gated: bool = True,
    rate_limiter: GlobalRateLimiter | None = None,
) -> Callable[[Request], Awaitable[JSONResponse]]:
    """Build the ``POST /oauth/register`` handler bound to specific stores.

    ``session_gated`` (default ``True``) keeps the offline-mode behavior:
    a prior ``SessionStore`` session is required. ``server.build_app``
    passes ``session_gated=False`` in the Cognito broker, where DCR is
    open per RFC 7591 (Cognito Hosted UI is the real auth gate).

    Returning a closure (rather than module-level state) means the same
    server can be stood up in tests with isolated stores per case.
    """

    async def register(request: Request) -> JSONResponse:
        # Global rolling-window ceiling FIRST — reject a flood before any
        # body parse / store work. Deliberately not per-IP (the edge WAF
        # does sound per-IP limiting; the client IP isn't trustworthy
        # here). KAN-83.
        if rate_limiter is not None:
            retry_after = rate_limiter.check()
            if retry_after is not None:
                emit_audit_event(
                    "oauth.client.registration.rate_limited",
                    retry_after=retry_after,
                )
                return JSONResponse(
                    {
                        "error": "rate_limit_exceeded",
                        "error_description": ("too many client registrations; retry later"),
                    },
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                )

        if session_gated:
            session = require_session(request, session_store)
            if session is None:
                return JSONResponse(
                    {
                        "error": "unauthorized",
                        "error_description": (
                            "Dynamic client registration requires an authenticated "
                            "session. Log in to VividScripts first."
                        ),
                    },
                    status_code=401,
                    headers={"WWW-Authenticate": 'Session realm="vividscripts-mcp"'},
                )
            owner_user_id = session.user_id
        else:
            owner_user_id = BROKER_CLIENT_OWNER

        try:
            payload: Any = await request.json()
        except ValueError:
            return _error_response(
                "invalid_client_metadata",
                "request body is not valid JSON",
                status_code=400,
            )

        try:
            req = RegistrationRequest.model_validate(payload)
        except ValidationError as exc:
            return _error_response(
                "invalid_client_metadata",
                exc.errors()[0].get("msg", "invalid client metadata"),
                status_code=400,
            )

        validation_error = _validate_metadata(req)
        if validation_error is not None:
            return validation_error

        client_id = secrets.token_urlsafe(16)
        issued_at = int(datetime.now(UTC).timestamp())
        client = RegisteredClient(
            client_id=client_id,
            issued_at=issued_at,
            owner_user_id=owner_user_id,
            redirect_uris=tuple(req.redirect_uris),
            token_endpoint_auth_method=req.token_endpoint_auth_method,
            grant_types=tuple(req.grant_types),
            response_types=tuple(req.response_types),
            client_name=req.client_name,
        )
        client_store.add(client)

        emit_audit_event(
            "oauth.client.registered",
            client_id=client_id,
            owner_user_id=owner_user_id,
            redirect_uris=req.redirect_uris,
            client_name=req.client_name,
        )

        body: dict[str, Any] = {
            "client_id": client_id,
            "client_id_issued_at": issued_at,
            "redirect_uris": req.redirect_uris,
            "token_endpoint_auth_method": req.token_endpoint_auth_method,
            "grant_types": req.grant_types,
            "response_types": req.response_types,
        }
        if req.client_name is not None:
            body["client_name"] = req.client_name

        return JSONResponse(body, status_code=201)

    return register
