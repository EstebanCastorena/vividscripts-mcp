"""Audit findings #8 + #13 — redirect_uri validation + match policy.

#8 — ``_is_safe_redirect_uri`` (oauth/dcr.py) currently uses
``str.startswith`` so ``http://localhost.attacker.com/cb`` is accepted as
loopback. We assert real loopback-host parsing: only the exact hosts
``127.0.0.1``, ``localhost``, ``::1`` over HTTP qualify, and URIs with
embedded credentials (``user:pass@host``) or fragments (``#anchor``) are
rejected on every scheme.

#13 — strict exact-match on ``redirect_uri`` is inconsistent with the
RFC 8252 §7.3 loopback port-flexibility we want native clients (Claude
Code, vs ``vividscripts-mcp serve``) to actually be able to use. We pick
the *implement-port-flexibility-for-loopback* side of the audit's
either/or: register on one ephemeral port, redeem on another, only when
the host is loopback. Non-loopback URIs remain strict exact-match
(security-positive).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from starlette.testclient import TestClient

from vividscripts_mcp.oauth.dcr import _is_safe_redirect_uri
from vividscripts_mcp.oauth.session import SESSION_COOKIE_NAME, MockSessionStore
from vividscripts_mcp.oauth.store import MockClientStore
from vividscripts_mcp.server import build_app

# ---------------------------------------------------------------------------
# Finding #8 — _is_safe_redirect_uri must reject non-loopback HTTP, embedded
# credentials, and fragments.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "uri",
    [
        # The headline #8 attack: looks like loopback to str.startswith but
        # resolves to attacker.com.
        "http://localhost.attacker.com/cb",
        "http://127.0.0.1.attacker.com/cb",
        # Embedded credentials carry the URI to a different netloc than a
        # naive reader expects (the real host is after the @).
        "http://user:pass@evil.com/cb",
        "https://localhost@evil.com/cb",
        "http://localhost:8080@evil.com/cb",
        # Fragments end up client-side after the OAuth dance — never a
        # legitimate part of a registered redirect_uri.
        "http://127.0.0.1:8080/cb#frag",
        "https://app.example.com/cb#frag",
        # Garbage that should never parse as a valid URL.
        "not-a-url",
        "javascript:alert(1)",
        "file:///etc/passwd",
        "http://",
    ],
)
def test_unsafe_redirect_uri_rejected(uri: str) -> None:
    assert _is_safe_redirect_uri(uri) is False, uri


@pytest.mark.parametrize(
    "uri",
    [
        "http://127.0.0.1:8080/cb",
        "http://127.0.0.1/cb",
        "http://localhost:8080/cb",
        "http://localhost/cb",
        "http://[::1]:8080/cb",
        "http://[::1]/cb",
        "https://app.example.com/cb",
        "https://app.example.com:8443/cb",
    ],
)
def test_safe_redirect_uri_accepted(uri: str) -> None:
    assert _is_safe_redirect_uri(uri) is True, uri


# ---------------------------------------------------------------------------
# Finding #8 — the same rejection has to fire end-to-end on /oauth/register
# (DCR), not only in the helper.
# ---------------------------------------------------------------------------


@pytest.fixture
def client_store() -> MockClientStore:
    return MockClientStore()


@pytest.fixture
def session_store() -> MockSessionStore:
    return MockSessionStore()


@pytest.fixture
def http(client_store: MockClientStore, session_store: MockSessionStore) -> Iterator[TestClient]:
    with TestClient(build_app(client_store=client_store, session_store=session_store)) as client:
        yield client


def test_dcr_rejects_localhost_attacker_dot_com(
    http: TestClient, session_store: MockSessionStore
) -> None:
    session = session_store.create(user_id="user-alpha")
    http.cookies.set(SESSION_COOKIE_NAME, session.session_id)
    response = http.post(
        "/oauth/register",
        json={"redirect_uris": ["http://localhost.attacker.com/cb"]},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_redirect_uri"


def test_dcr_rejects_embedded_credentials(
    http: TestClient, session_store: MockSessionStore
) -> None:
    session = session_store.create(user_id="user-alpha")
    http.cookies.set(SESSION_COOKIE_NAME, session.session_id)
    response = http.post(
        "/oauth/register",
        json={"redirect_uris": ["http://user:pass@127.0.0.1:8080/cb"]},
    )
    assert response.status_code == 400


def test_dcr_rejects_fragment(http: TestClient, session_store: MockSessionStore) -> None:
    session = session_store.create(user_id="user-alpha")
    http.cookies.set(SESSION_COOKIE_NAME, session.session_id)
    response = http.post(
        "/oauth/register",
        json={"redirect_uris": ["http://127.0.0.1:8080/cb#frag"]},
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Finding #13 — RFC 8252 §7.3 loopback port flexibility on /oauth/authorize.
# ---------------------------------------------------------------------------


def _register_loopback_client(http: TestClient, session_store: MockSessionStore) -> str:
    session = session_store.create(user_id="user-alpha")
    http.cookies.set(SESSION_COOKIE_NAME, session.session_id)
    response = http.post(
        "/oauth/register",
        json={"redirect_uris": ["http://127.0.0.1:8080/cb"]},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["client_id"])


def test_authorize_accepts_loopback_with_different_port(
    http: TestClient, session_store: MockSessionStore
) -> None:
    """RFC 8252 §7.3 — native clients pick ephemeral ports. The port must
    not be part of the exact-match check when the host is loopback."""
    client_id = _register_loopback_client(http, session_store)
    response = http.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": "http://127.0.0.1:54321/cb",
            "code_challenge": "abc" * 16,
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302, response.text


def test_authorize_rejects_non_loopback_port_mismatch(
    http: TestClient, session_store: MockSessionStore
) -> None:
    """Strict exact-match still applies to non-loopback hosts — port
    flexibility is loopback-only."""
    session = session_store.create(user_id="user-alpha")
    http.cookies.set(SESSION_COOKIE_NAME, session.session_id)
    response = http.post(
        "/oauth/register",
        json={"redirect_uris": ["https://app.example.com:8443/cb"]},
    )
    assert response.status_code == 201
    client_id = response.json()["client_id"]

    auth_response = http.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": "https://app.example.com:9999/cb",
            "code_challenge": "abc" * 16,
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )
    assert auth_response.status_code == 400


def test_authorize_rejects_loopback_path_mismatch(
    http: TestClient, session_store: MockSessionStore
) -> None:
    """Port flexibility doesn't extend to path — the redemption URI must
    still match the registered path (only the port is variable)."""
    client_id = _register_loopback_client(http, session_store)
    response = http.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": "http://127.0.0.1:8080/different-path",
            "code_challenge": "abc" * 16,
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )
    assert response.status_code == 400
