"""Sec-E / KAN-98 — duplicate Authorization headers are rejected (audit finding #22).

Starlette's :class:`Headers.get` returns the first match. If a client (or
an upstream that re-adds an Authorization header) sends two, the Bearer
middleware silently picks the first and ignores the second. That is a
request-smuggling adjacency: in any topology where an upstream and the
app disagree on which header wins, the security decision diverges from
what was authorized.

The audit's recommendation is to reject any request that presents more
than one Authorization header at the middleware boundary. Implementation
note: the rejection MUST happen before the token is validated — the
goal is to deny the *shape*, not to pick a winning token and validate it.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from collections.abc import Iterator
from typing import Any

import pytest
from starlette.testclient import TestClient

from vividscripts_mcp.oauth.codes import MockAuthCodeStore, MockAuthRequestStateStore
from vividscripts_mcp.oauth.keys import reset_signing_key
from vividscripts_mcp.oauth.session import MockSessionStore
from vividscripts_mcp.oauth.store import MockClientStore, RegisteredClient
from vividscripts_mcp.oauth.tokens import (
    DEFAULT_AUDIENCE,
    DEFAULT_ISSUER,
    MockRefreshTokenStore,
    mint_access_token,
)
from vividscripts_mcp.server import build_app

_CLIENT_ID = "test-client"
_REDIRECT_URI = "http://127.0.0.1:8080/callback"


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


@pytest.fixture(autouse=True)
def _fresh_key() -> Iterator[None]:
    reset_signing_key()
    yield
    reset_signing_key()


@pytest.fixture
def http() -> Iterator[TestClient]:
    client_store = MockClientStore()
    client_store.add(
        RegisteredClient(
            client_id=_CLIENT_ID,
            issued_at=1_700_000_000,
            owner_user_id="user-alpha",
            redirect_uris=(_REDIRECT_URI,),
            token_endpoint_auth_method="none",
            grant_types=("authorization_code", "refresh_token"),
            response_types=("code",),
            client_name="Claude Code",
        )
    )
    stores: dict[str, Any] = {
        "session_store": MockSessionStore(),
        "request_state_store": MockAuthRequestStateStore(),
        "code_store": MockAuthCodeStore(),
        "refresh_token_store": MockRefreshTokenStore(),
    }
    with TestClient(build_app(client_store=client_store, **stores)) as client:
        yield client


def _valid_token() -> str:
    token, _ = mint_access_token(
        user_id="user-alpha",
        client_id=_CLIENT_ID,
        issuer=DEFAULT_ISSUER,
        audience=DEFAULT_AUDIENCE,
    )
    return token


def _is_middleware_reject(response: Any) -> bool:
    """Return True iff the response shape matches an explicit middleware reject.

    The pre-fix path falls through to FastMCP, which 400s for unrelated
    reasons (missing JSON content-type, missing init body). We need to
    discriminate on the body — the middleware reject emits a JSON
    ``error_description`` that names ``Authorization``; FastMCP's error
    bodies don't.
    """
    if response.status_code != 400:
        return False
    body_lower = response.text.lower()
    return "authoriz" in body_lower


def test_two_authorization_headers_rejected_at_middleware(http: TestClient) -> None:
    """Two Authorization headers on a gated path return a middleware-shaped 400.

    Pre-fix: FastMCP's content-type 400 (``Invalid Content-Type header``)
    leaks through — same status code but doesn't mention the header. The
    test discriminates on the body.
    """
    token = _valid_token()
    response = http.post(
        "/mcp",
        headers=[
            ("authorization", f"Bearer {token}"),
            ("authorization", f"Bearer {token}"),
        ],
    )
    assert _is_middleware_reject(response), (
        f"expected middleware 400 naming Authorization; got "
        f"{response.status_code} body={response.text!r}"
    )


def test_two_different_authorization_headers_rejected(http: TestClient) -> None:
    """A valid + invalid pair must be denied just like two valid ones."""
    token = _valid_token()
    response = http.post(
        "/mcp",
        headers=[
            ("authorization", f"Bearer {token}"),
            ("authorization", "Bearer attacker-supplied"),
        ],
    )
    assert _is_middleware_reject(response), (
        f"expected middleware 400 naming Authorization; got "
        f"{response.status_code} body={response.text!r}"
    )


def test_two_authorization_headers_rejected_with_content_type(http: TestClient) -> None:
    """Even with valid JSON content-type, duplicate headers are rejected.

    Closes the loophole where a future caller fixes the content-type and
    accidentally re-enables the duplicate-header pass-through. The fix
    must reject the SHAPE, not just emit a coincidentally-similar 400.
    """
    token = _valid_token()
    response = http.post(
        "/mcp",
        headers=[
            ("authorization", f"Bearer {token}"),
            ("authorization", f"Bearer {token}"),
            ("content-type", "application/json"),
            ("accept", "application/json, text/event-stream"),
        ],
        json={"jsonrpc": "2.0", "method": "initialize", "id": 1},
    )
    assert _is_middleware_reject(response), (
        f"expected middleware 400 naming Authorization even with JSON content-type; "
        f"got {response.status_code} body={response.text!r}"
    )


def test_two_authorization_headers_on_public_path_still_pass(http: TestClient) -> None:
    """The duplicate-header reject must NOT apply to unauthenticated paths.

    ``/health`` and ``/.well-known/*`` should remain reachable; the
    audit's concern is the Bearer decision diverging across an upstream
    boundary, not a defensive 400 on every endpoint.
    """
    response = http.get(
        "/health",
        headers=[
            ("authorization", "Bearer one"),
            ("authorization", "Bearer two"),
        ],
    )
    assert response.status_code == 200


def test_single_authorization_header_still_works(http: TestClient) -> None:
    """Regression guard: the normal happy-path (one header) is unchanged."""
    response = http.get("/health", headers={"authorization": "Bearer ignored-on-public-path"})
    assert response.status_code == 200
