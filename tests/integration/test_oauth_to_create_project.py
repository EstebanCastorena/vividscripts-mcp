"""End-to-end OAuth → create_project integration test (KAN-54).

This is the canonical conformance test for Phase 1. It simulates the
exact dance Claude Code performs:

  1. POST /mcp (no auth)                  → 401 + WWW-Authenticate
  2. GET  /.well-known/oauth-protected-resource
                                          → 200 + PRM JSON
  3. POST /oauth/register (session-gated) → 201 + client_id
  4. Generate PKCE code_verifier + code_challenge
  5. GET  /oauth/authorize                → 302 to /_mock_idp/login
  6. POST /_mock_idp/login                → 302 to redirect_uri with ?code=
  7. POST /oauth/token                    → 200 + access_token + refresh_token
  8. POST /mcp (initialize, with Bearer)  → 200 + Mcp-Session-Id (SSE)
  9. POST /mcp (notifications/initialized)→ 202 Accepted
  10.POST /mcp (tools/call create_project)→ 200 SSE with the project info
  11.Backend has the project, owned by the authenticated user.

The test runs end-to-end against the real ASGI app via Starlette's
TestClient. The base_url is ``http://127.0.0.1:8000`` (not the default
``http://testserver``) so the request's ``Host`` header satisfies
FastMCP's default DNS-rebinding guard.

How to run manually
-------------------

The same flow can be driven by hand against a live server (useful for
debugging or for showing the dance to a reviewer):

1. Start the server:
       vividscripts-mcp serve --port 8000

2. Pre-seed a session in another Python process (Phase 1 mock IdP needs
   a session before DCR):
       from vividscripts_mcp.oauth.session import MockSessionStore
       # ... (or just exercise the OAuth side first then come back to
       #      DCR using the cookie the mock IdP issues).

3. POST /oauth/register, then walk steps 4-10 above with curl. The MCP
   wire calls require Accept: application/json, text/event-stream and
   Content-Type: application/json — Step 8's response carries the
   Mcp-Session-Id header you'll echo back on steps 9 and 10.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import urllib.parse
from collections.abc import Iterator
from typing import Any

import pytest
from starlette.testclient import TestClient

from vividscripts_mcp.adapters.mock import MockBackend
from vividscripts_mcp.oauth.codes import MockAuthCodeStore, MockAuthRequestStateStore
from vividscripts_mcp.oauth.keys import reset_signing_key
from vividscripts_mcp.oauth.session import (
    SESSION_COOKIE_NAME,
    MockSessionStore,
)
from vividscripts_mcp.oauth.store import MockClientStore
from vividscripts_mcp.oauth.tokens import MockRefreshTokenStore
from vividscripts_mcp.server import build_app

_REDIRECT_URI = "http://127.0.0.1:8080/callback"


@pytest.fixture(autouse=True)
def _fresh_key() -> Iterator[None]:
    reset_signing_key()
    yield
    reset_signing_key()


@pytest.fixture
def backend() -> MockBackend:
    return MockBackend(base_url="https://app.vividscripts.test")


@pytest.fixture
def stores(backend: MockBackend) -> dict[str, Any]:
    return {
        "backend": backend,
        "client_store": MockClientStore(),
        "session_store": MockSessionStore(),
        "request_state_store": MockAuthRequestStateStore(),
        "code_store": MockAuthCodeStore(),
        "refresh_token_store": MockRefreshTokenStore(),
    }


@pytest.fixture
def http(stores: dict[str, Any]) -> Iterator[TestClient]:
    # base_url uses an explicit port so the Host header matches FastMCP's
    # default DNS-rebinding allow-list (``127.0.0.1:*``).
    with TestClient(build_app(**stores), base_url="http://127.0.0.1:8000") as client:
        yield client


def _pkce_pair() -> tuple[str, str]:
    """RFC 7636-compliant (verifier, S256 challenge) pair."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _parse_sse_message(body: str) -> dict[str, Any]:
    """Extract the JSON payload from a Streamable HTTP SSE response.

    FastMCP's tools/call returns ``event: message\\ndata: <json>``. We
    don't need full SSE parsing — just the first ``data:`` line.
    """
    for line in body.splitlines():
        if line.startswith("data: "):
            return json.loads(line[len("data: ") :])  # type: ignore[no-any-return]
    raise AssertionError(f"no data: line in SSE body:\n{body[:200]}")


def test_full_oauth_to_create_project(
    http: TestClient,
    backend: MockBackend,
    stores: dict[str, Any],
) -> None:
    """Walk every public step of the Phase 1 OAuth dance and call create_project.

    Each assertion documents the contract for the step. A failure here
    indicates a regression in one of the 6 OAuth endpoints, the Bearer
    middleware, or the MCP tool dispatch — exactly the surface KAN-54
    is meant to lock down.
    """
    # -----------------------------------------------------------------
    # Step 1: an unauthenticated POST to /mcp surfaces the PRM pointer.
    # -----------------------------------------------------------------
    naked = http.post("/mcp", json={"hello": "world"})
    assert naked.status_code == 401
    challenge = naked.headers["WWW-Authenticate"]
    assert challenge.startswith("Bearer ")
    assert 'resource_metadata="' in challenge

    # -----------------------------------------------------------------
    # Step 2: the PRM document is served, with the fields KAN-48 promised.
    # -----------------------------------------------------------------
    prm_response = http.get("/.well-known/oauth-protected-resource")
    assert prm_response.status_code == 200
    prm = prm_response.json()
    assert prm.get("authorization_servers")
    assert prm["resource_signing_alg_values_supported"] == ["RS256"]

    # -----------------------------------------------------------------
    # Step 3: DCR is session-gated, so we pre-seed a session as if the
    # user were already logged in via the web app, then register.
    # -----------------------------------------------------------------
    session = stores["session_store"].create(user_id="user-alpha")
    http.cookies.set(SESSION_COOKIE_NAME, session.session_id)

    register_response = http.post(
        "/oauth/register",
        json={"redirect_uris": [_REDIRECT_URI], "client_name": "Claude Code"},
    )
    assert register_response.status_code == 201
    client_id = register_response.json()["client_id"]

    # -----------------------------------------------------------------
    # Step 4 + 5: PKCE pair + /oauth/authorize.
    # -----------------------------------------------------------------
    verifier, challenge_param = _pkce_pair()
    auth_response = http.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": _REDIRECT_URI,
            "code_challenge": challenge_param,
            "code_challenge_method": "S256",
            "state": "csrf-xyz",
        },
        follow_redirects=False,
    )
    assert auth_response.status_code == 302
    location = auth_response.headers["location"]
    assert location.startswith("/_mock_idp/login?request_id=")
    request_id = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)["request_id"][0]

    # -----------------------------------------------------------------
    # Step 6: mock IdP login completes. Receive auth code at redirect_uri.
    # -----------------------------------------------------------------
    login_response = http.post(
        "/_mock_idp/login",
        data={"request_id": request_id, "user_id": "user-alpha"},
        follow_redirects=False,
    )
    assert login_response.status_code == 302
    redirect = login_response.headers["location"]
    assert redirect.startswith(_REDIRECT_URI + "?")
    redirect_qs = urllib.parse.parse_qs(urllib.parse.urlparse(redirect).query)
    assert redirect_qs["state"] == ["csrf-xyz"]
    auth_code = redirect_qs["code"][0]

    # -----------------------------------------------------------------
    # Step 7: exchange the code for tokens with the PKCE verifier.
    # -----------------------------------------------------------------
    token_response = http.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": auth_code,
            "client_id": client_id,
            "redirect_uri": _REDIRECT_URI,
            "code_verifier": verifier,
        },
    )
    assert token_response.status_code == 200
    assert token_response.headers["content-type"].startswith("application/json")
    token_body = token_response.json()
    assert token_body["token_type"] == "Bearer"
    access_token = token_body["access_token"]
    refresh_token = token_body["refresh_token"]
    assert access_token and refresh_token

    # -----------------------------------------------------------------
    # Step 8: MCP initialize handshake. The Bearer guards /mcp, so this
    # is where KAN-52's validator gets exercised against a real token.
    # -----------------------------------------------------------------
    mcp_headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}",
    }
    init_response = http.post(
        "/mcp",
        headers=mcp_headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "kan-54-integration", "version": "0"},
            },
        },
    )
    assert init_response.status_code == 200
    mcp_session_id = init_response.headers.get("Mcp-Session-Id")
    assert mcp_session_id, "FastMCP must issue a session id on initialize"
    init_payload = _parse_sse_message(init_response.text)
    assert init_payload["id"] == 1
    assert init_payload["result"]["serverInfo"]["name"] == "vividscripts-mcp"

    # -----------------------------------------------------------------
    # Step 9: initialized notification (the spec requires this before
    # any tool/resource calls).
    # -----------------------------------------------------------------
    mcp_headers["Mcp-Session-Id"] = mcp_session_id
    notif_response = http.post(
        "/mcp",
        headers=mcp_headers,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    # 202 Accepted is the spec's expected response for a one-way notification.
    # FastMCP may also return 200; both are valid.
    assert notif_response.status_code in {200, 202}

    # -----------------------------------------------------------------
    # Step 10: invoke the create_project tool.
    # -----------------------------------------------------------------
    call_response = http.post(
        "/mcp",
        headers=mcp_headers,
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "create_project",
                "arguments": {
                    "story": "I lived alone for years. Or so I thought.",
                    "settings": {
                        "style": "dark_cinematic",
                        "voice": "female",
                        "dimension": "landscape",
                    },
                },
            },
        },
    )
    assert call_response.status_code == 200, call_response.text
    call_payload = _parse_sse_message(call_response.text)
    assert call_payload["id"] == 2
    assert "error" not in call_payload, f"tool call errored: {call_payload}"
    # FastMCP wraps the tool's return value in a content array AND surfaces
    # the structured payload under "structuredContent". We assert on the
    # structured form because it's stable across SDK versions.
    result = call_payload["result"]
    structured = result.get("structuredContent")
    assert structured is not None, f"no structuredContent in result: {result}"
    assert structured["project_id"]
    assert structured["editor_url"].startswith("https://app.vividscripts.test")

    # -----------------------------------------------------------------
    # Step 11: the project actually landed in the backend, scoped to
    # user-alpha (the authenticated subject of the access token).
    # -----------------------------------------------------------------
    project_id = structured["project_id"]
    persisted = backend.get_project(user_id="user-alpha", project_id=project_id)
    assert persisted.project_id == project_id


def test_bearer_protects_mcp_against_unauthenticated_calls(http: TestClient) -> None:
    """Standalone negative case: /mcp rejects requests without a valid token.

    The full flow above exercises the happy path; this test pins the
    Bearer enforcement on /mcp so a future refactor that accidentally
    disables the middleware fails loudly.
    """
    response = http.post(
        "/mcp",
        headers={
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        },
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    assert response.status_code == 401
    assert 'resource_metadata="' in response.headers["WWW-Authenticate"]


def test_bearer_with_invalid_token_blocks_mcp(http: TestClient) -> None:
    response = http.post(
        "/mcp",
        headers={
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "Authorization": "Bearer not.a.real.jwt",
        },
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    assert response.status_code == 401
    assert 'error="invalid_token"' in response.headers["WWW-Authenticate"]
