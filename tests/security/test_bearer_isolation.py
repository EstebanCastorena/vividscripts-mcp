"""Bearer middleware: context isolation + default-deny path gate (KAN-94).

Pins two HIGH-severity findings from the 2026-05-17 security audit:

* **#1 — contextvar reset.** ``BearerEnforcementMiddleware`` binds the
  validated user claims into a module-level ``ContextVar`` so downstream
  MCP tools can read the caller via :func:`require_user_claims`. Without
  a ``try/finally`` reset around the downstream ``await self.app(...)``,
  the bind leaks into whatever task/context invoked the middleware —
  a fail-to-previous-identity posture (not fail-closed). Any code path
  that reuses the calling task (background workers, sync ASGI harnesses,
  a future server that pools tasks) would see another user's stale
  identity.

* **#2 — default-deny path gate.** The original gate was
  ``path == "/mcp" or path.startswith("/mcp/")``, case-sensitive, on the
  raw ASGI path. Everything else passed through with no token check —
  ``/MCP``, dot-segments, legacy ``/sse``/``/messages`` transports, any
  future route the inner FastMCP app serves. Posture must be invert:
  default-deny, with an explicit allow-list for ``/health``,
  ``/.well-known/*``, ``/oauth/*``, and ``/_mock_idp/*``. Path
  normalization (case-fold, collapse empty/dot segments, reject
  ``..``) closes case/encoding/traversal bypass variants.

The two findings compound (an unauthenticated request through a path
that bypasses the gate would still read the prior request's stale
claims), so the regression tests live together and ship in one PR.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterator
from typing import Any

import pytest
from starlette.testclient import TestClient

from vividscripts_mcp.adapters.mock import MockBackend
from vividscripts_mcp.models import ProjectSettings
from vividscripts_mcp.oauth.bearer import UserClaims
from vividscripts_mcp.oauth.codes import MockAuthCodeStore, MockAuthRequestStateStore
from vividscripts_mcp.oauth.context import get_user_claims
from vividscripts_mcp.oauth.keys import reset_signing_key
from vividscripts_mcp.oauth.metadata import BearerEnforcementMiddleware
from vividscripts_mcp.oauth.session import MockSessionStore
from vividscripts_mcp.oauth.store import MockClientStore
from vividscripts_mcp.oauth.tokens import MockRefreshTokenStore, mint_access_token
from vividscripts_mcp.server import build_app

# ---------------------------------------------------------------------------
# Fixtures + tiny ASGI helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_key() -> Iterator[None]:
    """Each test gets a clean signing key so cross-test contamination is impossible."""
    reset_signing_key()
    yield
    reset_signing_key()


def _make_claims(sub: str) -> UserClaims:
    """A bare ``UserClaims`` for direct-middleware tests (no JWT round-trip)."""
    return UserClaims(
        sub=sub,
        client_id="client-test",
        scope=None,
        jti=f"jti-{sub}",
        exp=2**31 - 1,
        iat=0,
    )


def _mcp_scope(
    *,
    path: str = "/mcp",
    method: str = "POST",
    token: str | None = None,
) -> dict[str, Any]:
    """Minimal HTTP ASGI scope for the direct-middleware tests."""
    headers: list[tuple[bytes, bytes]] = [(b"host", b"testserver")]
    if token is not None:
        headers.append((b"authorization", f"Bearer {token}".encode("ascii")))
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "method": method,
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": headers,
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
        "root_path": "",
        "state": {},
    }


async def _empty_receive() -> dict[str, Any]:
    return {"type": "http.disconnect"}


def _capture_send() -> tuple[list[dict[str, Any]], Callable[[dict[str, Any]], Awaitable[None]]]:
    """Return ``(messages, send)`` — the send callable records every ASGI message."""
    messages: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    return messages, send


def _status_from(messages: list[dict[str, Any]]) -> int | None:
    for m in messages:
        if m.get("type") == "http.response.start":
            status = m.get("status")
            return int(status) if status is not None else None
    return None


def _header_from(messages: list[dict[str, Any]], name: str) -> str | None:
    needle = name.lower().encode("ascii")
    for m in messages:
        if m.get("type") == "http.response.start":
            for k, v in m.get("headers", []):
                if k.lower() == needle:
                    return v.decode("latin-1")
    return None


# ---------------------------------------------------------------------------
# Finding #1 — contextvar isolation
# ---------------------------------------------------------------------------


async def test_contextvar_does_not_leak_into_caller_after_request() -> None:
    """The middleware must reset the auth-context ``ContextVar`` on success.

    Drives the middleware directly so the caller (this test's task)
    *is* the parent context the bind would persist into. Failure mode:
    after the middleware returns, ``get_user_claims()`` in the caller
    still sees the request's claims.
    """
    alice = _make_claims("alice")
    inner_seen: list[UserClaims | None] = []

    async def inner(_scope: Any, _receive: Any, _send: Any) -> None:
        inner_seen.append(get_user_claims())

    mw = BearerEnforcementMiddleware(inner, validator=lambda _t: alice)

    assert get_user_claims() is None  # baseline
    _msgs, send = _capture_send()
    await mw(_mcp_scope(token="alice-token"), _empty_receive, send)

    assert inner_seen == [alice], "the inner app must see Alice during the request"
    assert get_user_claims() is None, (
        "BUG: Alice's claims leaked into the caller's context — "
        "middleware did not reset the contextvar Token after the request."
    )


async def test_unauth_request_does_not_inherit_prior_request_claims() -> None:
    """A second request (unauthenticated) must not read the first request's bind.

    Without the try/finally reset on the auth'd request, the caller's
    context still carries Alice. Then the unauth'd request — which the
    gate short-circuits with a 401 and never calls ``set_user_claims`` —
    leaves Alice's stale claims visible to any code that reads
    ``get_user_claims()`` in the caller's task. That is the
    fail-to-previous-identity posture the audit flagged.
    """
    alice = _make_claims("alice")

    async def inner(_scope: Any, _receive: Any, _send: Any) -> None:  # pragma: no cover
        return None

    mw_auth = BearerEnforcementMiddleware(inner, validator=lambda _t: alice)
    _msgs1, send1 = _capture_send()
    await mw_auth(_mcp_scope(token="alice-token"), _empty_receive, send1)

    # Unauth'd request follows. The gate 401s without binding, so the
    # *only* way Alice's claims could be visible here is via the leak
    # from request 1.
    mw_unauth = BearerEnforcementMiddleware(inner, validator=lambda _t: None)
    _msgs2, send2 = _capture_send()
    await mw_unauth(_mcp_scope(token=None), _empty_receive, send2)
    assert _status_from(_msgs2) == 401

    assert get_user_claims() is None, (
        "BUG: the second (unauth'd) request observed Alice's claims via "
        "the contextvar leak from request 1."
    )


async def test_contextvar_reset_even_when_inner_app_raises() -> None:
    """A downstream exception must NOT skip the contextvar reset.

    The ``try/finally`` is the whole guarantee — without it, an inner
    error mid-stream leaves a permanent stale bind.
    """
    alice = _make_claims("alice")

    class Boom(RuntimeError):
        pass

    async def inner(_scope: Any, _receive: Any, _send: Any) -> None:
        raise Boom("downstream blew up")

    mw = BearerEnforcementMiddleware(inner, validator=lambda _t: alice)
    _msgs, send = _capture_send()
    with pytest.raises(Boom):
        await mw(_mcp_scope(token="alice-token"), _empty_receive, send)

    assert get_user_claims() is None, (
        "BUG: an inner-app exception left Alice's claims bound — "
        "the reset must run in a finally block."
    )


# ---------------------------------------------------------------------------
# Finding #1 — interleaved two users via the real ASGI stack
# ---------------------------------------------------------------------------


def test_interleaved_two_users_each_see_their_own_projects() -> None:
    """Two access tokens (Alice + Bob), each tool call returns only the caller's data.

    Drives the *real* ASGI app via TestClient — the integration
    assertion the ticket asks for. TestClient runs each request in its
    own asyncio task, so per-task isolation does most of the work here;
    this pins the contract that even with HTTP keep-alive the gate +
    context binding never returns one user's projects to another.
    """
    stores: dict[str, Any] = {
        "backend": MockBackend(base_url="https://app.vividscripts.test"),
        "client_store": MockClientStore(),
        "session_store": MockSessionStore(),
        "request_state_store": MockAuthRequestStateStore(),
        "code_store": MockAuthCodeStore(),
        "refresh_token_store": MockRefreshTokenStore(),
    }
    backend: MockBackend = stores["backend"]

    settings = ProjectSettings(style="dark_cinematic", voice="female", dimension="landscape")
    alice_proj = backend.create_project(user_id="alice", story="alice's story", settings=settings)
    bob_proj = backend.create_project(user_id="bob", story="bob's story", settings=settings)

    alice_token, _ = mint_access_token(user_id="alice", client_id="cli")
    bob_token, _ = mint_access_token(user_id="bob", client_id="cli")

    with TestClient(build_app(**stores), base_url="http://127.0.0.1:8000") as http:
        alice_names = _list_project_names(http, alice_token)
        bob_names = _list_project_names(http, bob_token)
        # And again, interleaved, to catch any latent leak in the second round.
        assert _list_project_names(http, alice_token) == alice_names
        assert _list_project_names(http, bob_token) == bob_names

    assert alice_proj.project_name in alice_names
    assert bob_proj.project_name not in alice_names, (
        "BUG: Alice saw Bob's project — cross-tenant identity bleed."
    )
    assert bob_proj.project_name in bob_names
    assert alice_proj.project_name not in bob_names, (
        "BUG: Bob saw Alice's project — cross-tenant identity bleed."
    )


def _list_project_names(http: TestClient, token: str) -> list[str]:
    """Drive the MCP handshake + ``tools/call list_projects`` for one token."""
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    init = http.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "kan-94", "version": "0"},
            },
        },
    )
    assert init.status_code == 200, init.text
    # Stateless transport (KAN-123): no Mcp-Session-Id to echo — each
    # request is authorized solely by its Bearer token, which is exactly
    # the isolation boundary this test asserts.
    notif = http.post(
        "/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    assert notif.status_code in {200, 202}
    call = http.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "list_projects", "arguments": {}},
        },
    )
    assert call.status_code == 200, call.text
    for line in call.text.splitlines():
        if line.startswith("data: "):
            payload = json.loads(line[len("data: ") :])
            structured = payload["result"]["structuredContent"]
            # FastMCP wraps a bare-list return under ``result``.
            items = structured.get("result", structured)
            return [item["project_name"] for item in items]
    raise AssertionError(f"no data: line in SSE body: {call.text[:200]}")


# ---------------------------------------------------------------------------
# Finding #2 — default-deny path gate (bypass probes)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        # Case variant — bypassed the old case-sensitive ``startswith`` gate.
        "/MCP",
        "/Mcp",
        "/MCP/initialize",
        # Dot-segment / trailing-dot bypass attempts.
        "/mcp/.",
        "/mcp/./",
        # Legacy MCP transport paths the inner FastMCP app could grow
        # back (``/sse``, ``/messages``) plus arbitrary unrouted paths.
        "/sse",
        "/messages",
        "/messages/",
        "/admin",
        "/api/internal",
        # Same-prefix-but-not-an-allowlisted-route.
        "/oauth",
        "/healthz",  # superstring of /health must NOT be public.
        "/.well-known",  # the bare prefix without subpath must NOT be public.
    ],
)
def test_unauthed_non_public_paths_are_rejected_with_401(path: str) -> None:
    """Default-deny: anything outside the explicit allow-list requires Bearer."""
    with TestClient(build_app()) as client:
        response = client.get(path)
    assert response.status_code == 401, (
        f"BUG: {path!r} bypassed the Bearer gate (got {response.status_code}). "
        "Default-deny means every non-allow-listed path must 401 without a token."
    )
    challenge = response.headers["WWW-Authenticate"]
    assert challenge.startswith("Bearer "), challenge
    assert "resource_metadata=" in challenge


@pytest.mark.parametrize(
    "path,method",
    [
        # Explicit allow-list — each path must reach its handler. The
        # handler may itself respond with any status (200, 302, 400,
        # session-realm 401 for /oauth/register without a session, etc.)
        # but the response must NOT carry the Bearer-middleware's
        # ``WWW-Authenticate: Bearer …`` challenge.
        ("/health", "GET"),
        ("/.well-known/oauth-protected-resource", "GET"),
        ("/.well-known/jwks.json", "GET"),
        ("/oauth/register", "POST"),
        # Mock IdP login is the unauthenticated kickoff for the dev
        # OAuth dance; only mounted in offline mode (no Cognito).
        ("/_mock_idp/login", "GET"),
    ],
)
def test_public_allowlist_paths_are_not_blocked_by_bearer_gate(path: str, method: str) -> None:
    """The allow-list lets the legitimate unauthenticated endpoints through."""
    with TestClient(build_app()) as client:
        response = client.request(method, path)
    challenge = response.headers.get("WWW-Authenticate", "")
    assert not challenge.startswith("Bearer "), (
        f"BUG: allow-listed path {path!r} ({method}) was blocked by the Bearer "
        f"gate. WWW-Authenticate: {challenge!r}, status: {response.status_code}."
    )


async def test_path_normalization_rejects_dotdot_traversal() -> None:
    """``..`` segments must never let a request pose as an allow-listed path.

    A request to ``/mcp/../health`` resolves (post-normalization) to
    ``/health`` — exactly the bypass shape default-deny + a sane
    normalizer must block. We assert at the middleware level because
    Starlette's router may also reject such paths upstream; the gate
    must stand even if a future router change accepts them.
    """
    alice = _make_claims("alice")

    async def inner(_scope: Any, _receive: Any, _send: Any) -> None:  # pragma: no cover
        # Reached only if the gate (incorrectly) treats the path as public.
        raise AssertionError("gate should have rejected a ..-traversal path")

    mw = BearerEnforcementMiddleware(inner, validator=lambda _t: alice)
    msgs, send = _capture_send()
    await mw(_mcp_scope(path="/mcp/../health", token=None), _empty_receive, send)
    assert _status_from(msgs) == 401, (
        "BUG: ``/mcp/../health`` normalized to ``/health`` and bypassed the "
        "Bearer gate. Default-deny normalizer must reject ``..`` segments."
    )
