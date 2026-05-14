"""Inline mock Identity Provider for Phase 1.

Phase 3 (KAN-66) replaces this with redirects to Cognito Hosted UI on
``auth.vividscripts.ai``. Until then, ``/_mock_idp/login`` plays the
role of an IdP so the OAuth dance can be exercised end-to-end against
the Phase 1 dev server.

**This module must never be enabled in production builds.** The Phase 3
wiring removes the route entirely; nothing in this file is intended to
be wire-compatible with real Cognito beyond the redirect contract.

Flow:

1. ``GET /_mock_idp/login?request_id=<id>`` → minimal HTML form asking
   for a user_id. Real Cognito would prompt for username + password.
2. ``POST /_mock_idp/login`` with ``request_id`` + ``user_id`` →
   creates a session, generates an auth code, redirects to the
   originating client's ``redirect_uri`` with ``code`` + ``state``.
"""

from __future__ import annotations

import secrets
import urllib.parse
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from vividscripts_mcp.oauth.audit import emit_audit_event
from vividscripts_mcp.oauth.codes import (
    AUTH_CODE_TTL_SECONDS,
    AuthCode,
    AuthCodeStore,
    AuthRequestStateStore,
)
from vividscripts_mcp.oauth.session import SESSION_COOKIE_NAME, SessionStore

LOGIN_PATH = "/_mock_idp/login"

_LOGIN_FORM = """<!doctype html>
<html>
<head><title>VividScripts mock IdP</title></head>
<body>
<h1>Mock IdP (Phase 1 only)</h1>
<p>This dev endpoint stands in for Cognito Hosted UI. Phase 3 removes it.</p>
<form method="post" action="{action}">
  <label>User ID: <input name="user_id" value="user-alpha" required></label>
  <input type="hidden" name="request_id" value="{request_id}">
  <button type="submit">Log in</button>
</form>
</body>
</html>
"""


def make_login_handler(
    session_store: SessionStore,
    request_state_store: AuthRequestStateStore,
    code_store: AuthCodeStore,
) -> Callable[[Request], Awaitable[Response]]:
    """Build the ``GET|POST /_mock_idp/login`` handler bound to specific stores."""

    async def login(request: Request) -> Response:
        if request.method == "GET":
            request_id = request.query_params.get("request_id", "")
            return HTMLResponse(_LOGIN_FORM.format(action=LOGIN_PATH, request_id=request_id))

        form = await request.form()
        user_id = str(form.get("user_id", "")).strip()
        request_id = str(form.get("request_id", "")).strip()
        if not user_id or not request_id:
            return JSONResponse(
                {
                    "error": "invalid_request",
                    "error_description": "user_id and request_id are required",
                },
                status_code=400,
            )

        auth_req = request_state_store.consume(request_id)
        if auth_req is None:
            return JSONResponse(
                {
                    "error": "invalid_request",
                    "error_description": "unknown or expired request_id",
                },
                status_code=400,
            )

        session = session_store.create(user_id=user_id)

        code = secrets.token_urlsafe(32)
        now = int(datetime.now(UTC).timestamp())
        code_store.add(
            AuthCode(
                code=code,
                client_id=auth_req.client_id,
                redirect_uri=auth_req.redirect_uri,
                code_challenge=auth_req.code_challenge,
                code_challenge_method=auth_req.code_challenge_method,
                scope=auth_req.scope,
                user_id=user_id,
                expires_at=now + AUTH_CODE_TTL_SECONDS,
            )
        )

        emit_audit_event(
            "oauth.authorize.completed",
            client_id=auth_req.client_id,
            user_id=user_id,
        )

        redirect_params: dict[str, str] = {"code": code}
        if auth_req.state is not None:
            redirect_params["state"] = auth_req.state
        redirect_to = f"{auth_req.redirect_uri}?{urllib.parse.urlencode(redirect_params)}"

        response = RedirectResponse(redirect_to, status_code=302)
        # Same-site lax + httponly. Phase 1 dev server is HTTP, so we don't
        # set secure=True here; Phase 3 wiring enables it.
        response.set_cookie(
            SESSION_COOKIE_NAME,
            session.session_id,
            httponly=True,
            samesite="lax",
        )
        return response

    return login
