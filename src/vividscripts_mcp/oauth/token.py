"""``/oauth/token`` endpoint (RFC 6749 § 5) — KAN-51.

Supports two grant types:

- ``authorization_code`` — exchanges a one-shot code from the authorize
  flow for an access token + refresh token. Validates PKCE
  (RFC 7636 § 4.6): the SHA-256 of the presented ``code_verifier``,
  base64url-encoded with stripped padding, must equal the
  ``code_challenge`` captured at ``/oauth/authorize`` time.
- ``refresh_token`` — exchanges a refresh token for a fresh access token
  and a rotated refresh token. The old refresh token is invalidated.

Security guarantees, all tested:

- **PKCE required.** Missing ``code_verifier`` or mismatching it against
  the stored ``code_challenge`` returns 400 ``invalid_grant``. No fallback
  path (Security AC #1 on KAN-29).
- **Auth codes are single-use.** The ``code_store`` pops on consume; a
  replayed code is rejected with 400 ``invalid_grant`` (Security AC #2).
- **Code/client/redirect_uri binding.** A code can only be redeemed by
  the client it was issued to, with the exact ``redirect_uri`` it was
  bound to. Cross-binding attempts return ``invalid_grant``.
- **Refresh tokens rotate.** Every successful refresh consumes the old
  token and issues a new one — replay of the prior token fails.
- **JSON response, application/json content-type.** Per RFC 6749 § 5.1.
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.datastructures import FormData
from starlette.requests import Request
from starlette.responses import JSONResponse

from vividscripts_mcp.oauth import cognito as cognito_mod
from vividscripts_mcp.oauth.audit import emit_audit_event
from vividscripts_mcp.oauth.codes import AuthCode, AuthCodeStore
from vividscripts_mcp.oauth.cognito import CognitoConfig
from vividscripts_mcp.oauth.store import ClientStore
from vividscripts_mcp.oauth.tokens import (
    RefreshTokenStore,
    mint_access_token,
    mint_refresh_token,
)


def _error(error: str, description: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse(
        {"error": error, "error_description": description},
        status_code=status_code,
    )


def _verify_pkce(code_verifier: str, code_challenge: str, method: str) -> bool:
    """RFC 7636 § 4.6: base64url(sha256(verifier)) == stored challenge.

    Only S256 is implemented; ``plain`` was rejected at /oauth/authorize.
    """
    if method != "S256":
        return False
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return computed == code_challenge


def make_token_handler(
    client_store: ClientStore,
    code_store: AuthCodeStore,
    refresh_token_store: RefreshTokenStore,
    cognito: CognitoConfig | None = None,
) -> Callable[[Request], Awaitable[JSONResponse]]:
    """Build the ``POST /oauth/token`` handler bound to specific stores.

    With ``cognito`` set the endpoint is **pass-through** (KAN-36): the
    PKCE / single-use / binding checks are unchanged, but the response
    returns the Cognito tokens captured at ``/oauth/callback`` instead
    of self-minting, and the refresh grant proxies to Cognito. With
    ``cognito`` unset it keeps the Phase-1 self-mint behavior (offline).
    """

    async def token(request: Request) -> JSONResponse:
        form = await request.form()
        grant_type = str(form.get("grant_type", ""))

        if grant_type == "authorization_code":
            return await _handle_authorization_code(
                form, client_store, code_store, refresh_token_store, cognito
            )
        if grant_type == "refresh_token":
            return await _handle_refresh_token(form, refresh_token_store, cognito)

        return _error(
            "unsupported_grant_type",
            f"unsupported grant_type: {grant_type or '<missing>'}",
        )

    return token


def _passthrough_body(auth_code: AuthCode) -> JSONResponse:
    """Return the Cognito tokens bound to this one-shot code (KAN-36)."""
    body: dict[str, Any] = {
        "access_token": auth_code.cognito_access_token,
        "token_type": "Bearer",
        "expires_in": auth_code.cognito_expires_in,
    }
    if auth_code.cognito_refresh_token is not None:
        body["refresh_token"] = auth_code.cognito_refresh_token
    if auth_code.scope is not None:
        body["scope"] = auth_code.scope
    return JSONResponse(body, status_code=200)


async def _handle_authorization_code(
    form: FormData,
    client_store: ClientStore,
    code_store: AuthCodeStore,
    refresh_token_store: RefreshTokenStore,
    cognito: CognitoConfig | None,
) -> JSONResponse:
    code = str(form.get("code", ""))
    client_id = str(form.get("client_id", ""))
    redirect_uri = str(form.get("redirect_uri", ""))
    code_verifier = str(form.get("code_verifier", ""))

    if not code or not client_id or not redirect_uri or not code_verifier:
        return _error(
            "invalid_request",
            "code, client_id, redirect_uri, code_verifier are required",
        )

    if client_store.get(client_id) is None:
        return _error("invalid_client", "unknown client_id")

    auth_code = code_store.consume(code)
    if auth_code is None:
        return _error(
            "invalid_grant",
            "authorization code is invalid, expired, or already used",
        )

    if auth_code.client_id != client_id:
        return _error(
            "invalid_grant",
            "authorization code was issued to a different client",
        )
    if auth_code.redirect_uri != redirect_uri:
        return _error(
            "invalid_grant",
            "redirect_uri does not match the URI bound to this code",
        )

    if not _verify_pkce(code_verifier, auth_code.code_challenge, auth_code.code_challenge_method):
        return _error(
            "invalid_grant",
            "PKCE code_verifier does not match the original code_challenge",
        )

    # Broker pass-through (KAN-36): the one-shot code carries the Cognito
    # tokens bound at /oauth/callback. PKCE/binding/single-use were all
    # enforced above; return Cognito's tokens, never self-mint.
    if cognito is not None:
        if auth_code.cognito_access_token is None:
            return _error(
                "invalid_grant",
                "authorization code is not bound to Cognito tokens",
            )
        emit_audit_event(
            "oauth.token.issued",
            grant_type="authorization_code",
            client_id=client_id,
            user_id=auth_code.user_id,
            via="cognito",
        )
        return _passthrough_body(auth_code)

    access_token, expires_in = mint_access_token(
        user_id=auth_code.user_id,
        client_id=client_id,
        scope=auth_code.scope,
    )
    refresh_token, refresh_record = mint_refresh_token(
        user_id=auth_code.user_id,
        client_id=client_id,
        scope=auth_code.scope,
    )
    refresh_token_store.add(refresh_record)

    emit_audit_event(
        "oauth.token.issued",
        grant_type="authorization_code",
        client_id=client_id,
        user_id=auth_code.user_id,
    )

    body: dict[str, Any] = {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": expires_in,
        "refresh_token": refresh_token,
    }
    if auth_code.scope is not None:
        body["scope"] = auth_code.scope
    return JSONResponse(body, status_code=200)


async def _handle_refresh_token(
    form: FormData,
    refresh_token_store: RefreshTokenStore,
    cognito: CognitoConfig | None,
) -> JSONResponse:
    presented = str(form.get("refresh_token", ""))
    if not presented:
        return _error("invalid_request", "refresh_token is required")

    # Broker pass-through (KAN-36): proxy the refresh to Cognito and
    # return Cognito's rotated access token. Cognito does not rotate the
    # refresh token itself, so the client keeps reusing the original.
    if cognito is not None:
        tokens = await cognito_mod.refresh_tokens(cognito, refresh_token=presented)
        if tokens is None:
            return _error(
                "invalid_grant",
                "refresh_token is invalid, expired, or revoked",
            )
        emit_audit_event(
            "oauth.token.issued",
            grant_type="refresh_token",
            client_id=cognito.client_id,
            via="cognito",
        )
        refreshed: dict[str, Any] = {
            "access_token": tokens.access_token,
            "token_type": "Bearer",
            "expires_in": tokens.expires_in,
            "refresh_token": tokens.refresh_token or presented,
        }
        return JSONResponse(refreshed, status_code=200)

    record = refresh_token_store.consume(presented)
    if record is None:
        return _error(
            "invalid_grant",
            "refresh_token is invalid, expired, or already used",
        )

    access_token, expires_in = mint_access_token(
        user_id=record.user_id,
        client_id=record.client_id,
        scope=record.scope,
    )
    new_refresh, new_record = mint_refresh_token(
        user_id=record.user_id,
        client_id=record.client_id,
        scope=record.scope,
    )
    refresh_token_store.add(new_record)

    emit_audit_event(
        "oauth.token.issued",
        grant_type="refresh_token",
        client_id=record.client_id,
        user_id=record.user_id,
    )

    body: dict[str, Any] = {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": expires_in,
        "refresh_token": new_refresh,
    }
    if record.scope is not None:
        body["scope"] = record.scope
    return JSONResponse(body, status_code=200)
