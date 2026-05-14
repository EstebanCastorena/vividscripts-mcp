"""Dynamic Client Registration endpoint (RFC 7591) — KAN-49.

Implements ``POST /oauth/register`` with these guarantees:

- Session-gated: rejects unauthenticated requests with 401 (mitigates
  KAN-46 / threat 1.4 — DCR replay attack). The user must already hold
  a session in the configured ``SessionStore``.
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
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

from vividscripts_mcp.oauth.audit import emit_audit_event
from vividscripts_mcp.oauth.session import SessionStore, require_session
from vividscripts_mcp.oauth.store import ClientStore, RegisteredClient

# Allow-lists: what we accept from registration requests. Anything not in
# these sets is rejected with invalid_client_metadata rather than silently
# stored — the principle is that the server's allowed surface is the source
# of truth, not the client's request.
_ALLOWED_GRANT_TYPES = frozenset({"authorization_code", "refresh_token"})
_ALLOWED_RESPONSE_TYPES = frozenset({"code"})
_ALLOWED_AUTH_METHODS = frozenset({"none", "client_secret_basic", "client_secret_post"})


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

    Public-web HTTP redirect URIs are rejected — tokens travelling over
    plaintext to an attacker-controlled domain is the classic OAuth leak.
    """
    if uri.startswith("https://"):
        return True
    loopback_prefixes = ("http://127.0.0.1", "http://localhost", "http://[::1]")
    return any(uri.startswith(prefix) for prefix in loopback_prefixes)


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


def make_register_handler(
    client_store: ClientStore,
    session_store: SessionStore,
) -> Callable[[Request], Awaitable[JSONResponse]]:
    """Build the ``POST /oauth/register`` handler bound to specific stores.

    Returning a closure (rather than module-level state) means the same
    server can be stood up in tests with isolated stores per case.
    """

    async def register(request: Request) -> JSONResponse:
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
            owner_user_id=session.user_id,
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
            owner_user_id=session.user_id,
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
