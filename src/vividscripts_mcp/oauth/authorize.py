"""``/oauth/authorize`` endpoint (RFC 6749 § 4.1 + RFC 7636 PKCE) — KAN-50.

The endpoint validates an authorization request and, on success, stores
the pending state and redirects the user agent to the mock IdP login
page. The mock IdP completes the flow by generating an auth code and
redirecting back to the client's registered ``redirect_uri``.

Security-relevant guarantees, all tested:

- **PKCE is mandatory.** Requests missing ``code_challenge`` or with
  ``code_challenge_method != "S256"`` are rejected. ``plain`` is
  explicitly refused — no fallback (Security AC #1 on KAN-29).
- **redirect_uri is exact-match.** No prefix, glob, or wildcard. A
  request whose ``redirect_uri`` isn't in the client's registered set
  returns 400 *without* redirecting — never hand a code/error to an
  attacker-controlled URI (Security AC #3, RFC 6749 § 4.1.2.1).
- **Single-use bookkeeping.** The pending state is stored under a fresh
  ``request_id`` consumed exactly once by the mock IdP callback.

All errors return 400 with a JSON body containing ``error`` and
``error_description`` (matching the KAN-50 acceptance criteria). A
spec-strict variant — redirect with ``error=...`` once ``redirect_uri``
is validated — is intentionally deferred to Phase 3 where the real
Cognito Hosted UI handles the user-visible failure path.
"""

from __future__ import annotations

import secrets
import urllib.parse
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from vividscripts_mcp.oauth.codes import (
    AUTH_REQUEST_TTL_SECONDS,
    AuthRequestState,
    AuthRequestStateStore,
)
from vividscripts_mcp.oauth.cognito import CognitoConfig
from vividscripts_mcp.oauth.store import ClientStore

#: Where ``/oauth/authorize`` sends the user agent in **offline** mode
#: (no Cognito configured). The broker path redirects to Cognito Hosted
#: UI instead; the ``request_id`` survives both as the round-trip nonce.
MOCK_IDP_LOGIN_PATH = "/_mock_idp/login"


def _error(error: str, description: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse(
        {"error": error, "error_description": description},
        status_code=status_code,
    )


def make_authorize_handler(
    client_store: ClientStore,
    request_state_store: AuthRequestStateStore,
    cognito: CognitoConfig | None = None,
) -> Callable[[Request], Awaitable[Response]]:
    """Build the ``GET /oauth/authorize`` handler bound to specific stores.

    When ``cognito`` is set the handler delegates authentication to
    Cognito Hosted UI (the broker path, KAN-85): it stores the pending
    request and 302s the browser to Cognito's authorize endpoint with
    the package's ``/oauth/callback`` as Cognito's ``redirect_uri`` and
    the ``request_id`` as the round-trip ``state``. With ``cognito``
    unset it keeps the Phase-1 mock-IdP redirect for offline use.
    """

    async def authorize(request: Request) -> Response:
        params = request.query_params

        client_id = params.get("client_id")
        if not client_id:
            return _error("invalid_request", "client_id is required")
        client = client_store.get(client_id)
        if client is None:
            return _error("invalid_client", "unknown client_id")

        redirect_uri = params.get("redirect_uri")
        if not redirect_uri:
            return _error("invalid_request", "redirect_uri is required")
        # Exact match — Security AC #3.
        if redirect_uri not in client.redirect_uris:
            return _error(
                "invalid_request",
                "redirect_uri does not match the client's registered URIs",
            )

        response_type = params.get("response_type")
        if response_type != "code":
            return _error(
                "unsupported_response_type",
                "only response_type=code is supported",
            )

        # PKCE — Security AC #1.
        code_challenge = params.get("code_challenge")
        if not code_challenge:
            return _error(
                "invalid_request",
                "PKCE code_challenge is required",
            )
        code_challenge_method = params.get("code_challenge_method")
        if code_challenge_method != "S256":
            return _error(
                "invalid_request",
                "PKCE code_challenge_method must be S256",
            )

        state = params.get("state")  # optional, echoed back; may be None
        scope = params.get("scope")

        request_id = secrets.token_urlsafe(24)
        now = int(datetime.now(UTC).timestamp())
        request_state_store.add(
            AuthRequestState(
                request_id=request_id,
                client_id=client_id,
                redirect_uri=redirect_uri,
                state=state,
                code_challenge=code_challenge,
                code_challenge_method=code_challenge_method,
                scope=scope,
                expires_at=now + AUTH_REQUEST_TTL_SECONDS,
            )
        )

        if cognito is not None:
            cognito_query = urllib.parse.urlencode(
                {
                    "response_type": "code",
                    "client_id": cognito.client_id,
                    "redirect_uri": cognito.callback_url,
                    "scope": " ".join(cognito.scopes),
                    "state": request_id,
                }
            )
            return RedirectResponse(
                f"{cognito.authorize_endpoint}?{cognito_query}",
                status_code=302,
            )

        return RedirectResponse(
            f"{MOCK_IDP_LOGIN_PATH}?request_id={request_id}",
            status_code=302,
        )

    return authorize
