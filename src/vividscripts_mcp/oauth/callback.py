"""``/oauth/callback`` — Cognito's redirect target in the broker (KAN-85).

Step 3 of the broker flow (see :mod:`vividscripts_mcp.oauth.cognito`):
after the user authenticates at Cognito Hosted UI, Cognito redirects
the browser here with its own ``code`` + the ``state`` we round-tripped
(the pending ``request_id``). This handler:

1. Looks up the pending :class:`AuthRequestState` by ``state`` — this
   is single-use and carries the original client's PKCE challenge,
   ``redirect_uri``, and CSRF ``state``.
2. Exchanges Cognito's code for Cognito tokens at Cognito's token
   endpoint (confidential, client-secret — :func:`cognito.exchange_code`).
3. Mints the package's **own** one-shot :class:`AuthCode`, bound to the
   original client + PKCE challenge + the Cognito tokens (pass-through,
   KAN-36).
4. 302s the browser back to the *client's* registered ``redirect_uri``
   with that code and the client's original ``state``.

``/oauth/token`` then verifies PKCE as before and returns the Cognito
tokens carried on the code — the package never mints its own.
"""

from __future__ import annotations

import secrets
import urllib.parse
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from vividscripts_mcp.oauth import cognito as cognito_mod
from vividscripts_mcp.oauth.audit import emit_audit_event
from vividscripts_mcp.oauth.codes import (
    AUTH_CODE_TTL_SECONDS,
    AuthCode,
    AuthCodeStore,
    AuthRequestStateStore,
)
from vividscripts_mcp.oauth.cognito import CognitoConfig


def _error(error: str, description: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse(
        {"error": error, "error_description": description},
        status_code=status_code,
    )


def make_callback_handler(
    request_state_store: AuthRequestStateStore,
    code_store: AuthCodeStore,
    cognito: CognitoConfig,
) -> Callable[[Request], Awaitable[Response]]:
    """Build the ``GET /oauth/callback`` handler (broker mode only)."""

    async def callback(request: Request) -> Response:
        params = request.query_params

        # Cognito surfaces auth failures as ?error=...; there is no
        # client redirect_uri to forward to until we resolve state, so
        # fail closed with a plain OAuth error.
        cognito_error = params.get("error")
        if cognito_error:
            return _error(
                "access_denied",
                f"Cognito authentication failed: {cognito_error}",
            )

        state = params.get("state")
        if not state:
            return _error("invalid_request", "state is required")
        code = params.get("code")
        if not code:
            return _error("invalid_request", "code is required")

        # Single-use: consume the pending request bound at /oauth/authorize.
        auth_req = request_state_store.consume(state)
        if auth_req is None:
            return _error(
                "invalid_request",
                "unknown or expired authorization request",
            )

        tokens = await cognito_mod.exchange_code(
            cognito,
            code=code,
            redirect_uri=cognito.callback_url,
        )
        if tokens is None:
            return _error(
                "invalid_grant",
                "Cognito authorization code exchange failed",
            )

        user_id = cognito_mod.subject_from_token(tokens.access_token)
        if user_id is None:
            return _error(
                "invalid_grant",
                "Cognito token is missing a subject claim",
            )

        package_code = secrets.token_urlsafe(32)
        now = int(datetime.now(UTC).timestamp())
        code_store.add(
            AuthCode(
                code=package_code,
                client_id=auth_req.client_id,
                redirect_uri=auth_req.redirect_uri,
                code_challenge=auth_req.code_challenge,
                code_challenge_method=auth_req.code_challenge_method,
                scope=auth_req.scope,
                user_id=user_id,
                expires_at=now + AUTH_CODE_TTL_SECONDS,
                cognito_access_token=tokens.access_token,
                cognito_refresh_token=tokens.refresh_token,
                cognito_expires_in=tokens.expires_in,
            )
        )

        emit_audit_event(
            "oauth.authorize.completed",
            client_id=auth_req.client_id,
            user_id=user_id,
            via="cognito",
        )

        redirect_params: dict[str, str] = {"code": package_code}
        if auth_req.state is not None:
            redirect_params["state"] = auth_req.state
        redirect_to = f"{auth_req.redirect_uri}?{urllib.parse.urlencode(redirect_params)}"
        return RedirectResponse(redirect_to, status_code=302)

    return callback
