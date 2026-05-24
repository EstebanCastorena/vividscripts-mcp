"""Sec-E / KAN-98 — uniform ``invalid_grant`` at /oauth/token (audit finding #23).

Returning ``invalid_client`` for unknown ``client_id`` and ``invalid_grant``
for unknown ``code`` lets a client enumerate registered ``client_id`` values
by comparing the two error codes. The risk is informational (``client_id``
is not a secret per RFC 6749 § 2.2) but the differential is also free to
remove and the audit recommended a uniform ``invalid_grant`` response.

The pre-existing ``test_code_bound_to_client_id`` allows either error; this
file pins the post-fix policy of ``invalid_grant`` everywhere — clients
distinguishing between the two then have nothing to enumerate against.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import urllib.parse
from collections.abc import Iterator
from typing import Any

import pytest
from starlette.testclient import TestClient

from vividscripts_mcp.oauth.codes import MockAuthCodeStore, MockAuthRequestStateStore
from vividscripts_mcp.oauth.keys import reset_signing_key
from vividscripts_mcp.oauth.session import MockSessionStore
from vividscripts_mcp.oauth.store import MockClientStore, RegisteredClient
from vividscripts_mcp.oauth.tokens import MockRefreshTokenStore
from vividscripts_mcp.server import build_app

_REDIRECT_URI = "http://127.0.0.1:8080/callback"
_CLIENT_ID = "test-client"


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _registered_client() -> RegisteredClient:
    return RegisteredClient(
        client_id=_CLIENT_ID,
        issued_at=1_700_000_000,
        owner_user_id="user-alpha",
        redirect_uris=(_REDIRECT_URI,),
        token_endpoint_auth_method="none",
        grant_types=("authorization_code", "refresh_token"),
        response_types=("code",),
        client_name="Claude Code",
    )


@pytest.fixture(autouse=True)
def _fresh_key() -> Iterator[None]:
    reset_signing_key()
    yield
    reset_signing_key()


@pytest.fixture
def http() -> Iterator[TestClient]:
    client_store = MockClientStore()
    client_store.add(_registered_client())
    stores: dict[str, Any] = {
        "session_store": MockSessionStore(),
        "request_state_store": MockAuthRequestStateStore(),
        "code_store": MockAuthCodeStore(),
        "refresh_token_store": MockRefreshTokenStore(),
    }
    with TestClient(build_app(client_store=client_store, **stores)) as client:
        yield client


def _complete_authorize(http: TestClient, challenge: str) -> str:
    auth_response = http.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": _CLIENT_ID,
            "redirect_uri": _REDIRECT_URI,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "csrf-xyz",
        },
        follow_redirects=False,
    )
    request_id = urllib.parse.parse_qs(
        urllib.parse.urlparse(auth_response.headers["location"]).query
    )["request_id"][0]

    login_response = http.post(
        "/_mock_idp/login",
        data={"request_id": request_id, "user_id": "user-alpha"},
        follow_redirects=False,
    )
    return urllib.parse.parse_qs(urllib.parse.urlparse(login_response.headers["location"]).query)[
        "code"
    ][0]


# ---------------------------------------------------------------------
# Unknown client_id collapses to invalid_grant
# ---------------------------------------------------------------------


def test_unknown_client_id_returns_invalid_grant(http: TestClient) -> None:
    """An unknown client_id no longer returns ``invalid_client`` — it returns ``invalid_grant``."""
    verifier, challenge = _pkce_pair()
    code = _complete_authorize(http, challenge)

    response = http.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": "completely-unknown-client",
            "redirect_uri": _REDIRECT_URI,
            "code_verifier": verifier,
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant", (
        "audit finding #23: token-endpoint failures must uniformly return "
        f"invalid_grant; got {response.json()['error']!r}"
    )


def test_invalid_code_returns_invalid_grant(http: TestClient) -> None:
    """Known client + bogus code → invalid_grant (regression guard)."""
    response = http.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": "bogus-code-value",
            "client_id": _CLIENT_ID,
            "redirect_uri": _REDIRECT_URI,
            "code_verifier": secrets.token_urlsafe(48),
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


def test_unknown_client_and_bogus_code_indistinguishable(http: TestClient) -> None:
    """The enumeration oracle is closed: both shapes produce identical error codes."""
    bad_client = http.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": "bogus-code",
            "client_id": "unknown-client",
            "redirect_uri": _REDIRECT_URI,
            "code_verifier": secrets.token_urlsafe(48),
        },
    )
    bad_code = http.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": "bogus-code",
            "client_id": _CLIENT_ID,
            "redirect_uri": _REDIRECT_URI,
            "code_verifier": secrets.token_urlsafe(48),
        },
    )
    assert bad_client.json()["error"] == bad_code.json()["error"] == "invalid_grant"


def test_happy_path_still_works(http: TestClient) -> None:
    """Regression guard: making errors uniform did not break the success path."""
    verifier, challenge = _pkce_pair()
    code = _complete_authorize(http, challenge)
    response = http.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": _CLIENT_ID,
            "redirect_uri": _REDIRECT_URI,
            "code_verifier": verifier,
        },
    )
    assert response.status_code == 200
    assert "access_token" in response.json()
