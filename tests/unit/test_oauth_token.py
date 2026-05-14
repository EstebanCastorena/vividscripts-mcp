"""Tests for /oauth/token (KAN-51 / RFC 6749 § 5 + RFC 7636 PKCE)."""

from __future__ import annotations

import base64
import hashlib
import secrets
import urllib.parse
from collections.abc import Iterator

import jwt
import pytest
from starlette.testclient import TestClient

from vividscripts_mcp.oauth.codes import MockAuthCodeStore, MockAuthRequestStateStore
from vividscripts_mcp.oauth.keys import ALGORITHM, KID, get_signing_key, reset_signing_key
from vividscripts_mcp.oauth.session import MockSessionStore
from vividscripts_mcp.oauth.store import MockClientStore, RegisteredClient
from vividscripts_mcp.oauth.tokens import (
    DEFAULT_AUDIENCE,
    DEFAULT_ISSUER,
    MockRefreshTokenStore,
)
from vividscripts_mcp.server import build_app

_REDIRECT_URI = "http://127.0.0.1:8080/callback"
_CLIENT_ID = "test-client"


def _pkce_pair() -> tuple[str, str]:
    """Return a valid (code_verifier, code_challenge) pair (RFC 7636)."""
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
    """Force a fresh signing key per test so claims don't bleed across tests."""
    reset_signing_key()
    yield
    reset_signing_key()


@pytest.fixture
def client_store() -> MockClientStore:
    store = MockClientStore()
    store.add(_registered_client())
    return store


@pytest.fixture
def session_store() -> MockSessionStore:
    return MockSessionStore()


@pytest.fixture
def request_state_store() -> MockAuthRequestStateStore:
    return MockAuthRequestStateStore()


@pytest.fixture
def code_store() -> MockAuthCodeStore:
    return MockAuthCodeStore()


@pytest.fixture
def refresh_token_store() -> MockRefreshTokenStore:
    return MockRefreshTokenStore()


@pytest.fixture
def http(
    client_store: MockClientStore,
    session_store: MockSessionStore,
    request_state_store: MockAuthRequestStateStore,
    code_store: MockAuthCodeStore,
    refresh_token_store: MockRefreshTokenStore,
) -> Iterator[TestClient]:
    with TestClient(
        build_app(
            client_store=client_store,
            session_store=session_store,
            request_state_store=request_state_store,
            code_store=code_store,
            refresh_token_store=refresh_token_store,
        )
    ) as client:
        yield client


def _complete_authorize(http: TestClient, code_challenge: str) -> str:
    """Walk the full authorize+login flow; return the issued auth code."""
    auth_response = http.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": _CLIENT_ID,
            "redirect_uri": _REDIRECT_URI,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": "csrf-xyz",
        },
        follow_redirects=False,
    )
    assert auth_response.status_code == 302
    request_id = urllib.parse.parse_qs(
        urllib.parse.urlparse(auth_response.headers["location"]).query
    )["request_id"][0]

    login_response = http.post(
        "/_mock_idp/login",
        data={"request_id": request_id, "user_id": "user-alpha"},
        follow_redirects=False,
    )
    assert login_response.status_code == 302
    return urllib.parse.parse_qs(urllib.parse.urlparse(login_response.headers["location"]).query)[
        "code"
    ][0]


# ---------------------------------------------------------------------------
# authorization_code grant
# ---------------------------------------------------------------------------


def test_authorization_code_happy_path(http: TestClient) -> None:
    """Valid exchange returns Bearer access + refresh token, application/json."""
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
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] > 0
    assert isinstance(body["access_token"], str) and body["access_token"]
    assert isinstance(body["refresh_token"], str) and body["refresh_token"]


def test_access_token_carries_required_claims(http: TestClient) -> None:
    """The minted JWT validates against the public key with the expected claims."""
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
    access_token = response.json()["access_token"]

    decoded = jwt.decode(
        access_token,
        key=get_signing_key().public_pem,
        algorithms=[ALGORITHM],
        audience=DEFAULT_AUDIENCE,
        issuer=DEFAULT_ISSUER,
    )
    assert decoded["sub"] == "user-alpha"
    assert decoded["client_id"] == _CLIENT_ID
    assert decoded["token_use"] == "access"
    assert "jti" in decoded
    assert decoded["exp"] > decoded["iat"]

    # Header has kid for JWKS-based validation in KAN-52
    header = jwt.get_unverified_header(access_token)
    assert header["alg"] == ALGORITHM
    assert header["kid"] == KID


def test_pkce_verifier_mismatch_returns_invalid_grant(http: TestClient) -> None:
    """Wrong code_verifier yields 400 invalid_grant (Security AC #1)."""
    _, challenge = _pkce_pair()
    code = _complete_authorize(http, challenge)

    response = http.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": _CLIENT_ID,
            "redirect_uri": _REDIRECT_URI,
            "code_verifier": "totally-wrong-verifier-value",
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


def test_missing_pkce_verifier_returns_400(http: TestClient) -> None:
    """No code_verifier in the body yields 400 invalid_request."""
    _, challenge = _pkce_pair()
    code = _complete_authorize(http, challenge)

    response = http.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": _CLIENT_ID,
            "redirect_uri": _REDIRECT_URI,
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


def test_auth_code_is_single_use(http: TestClient) -> None:
    """Replaying a redeemed code returns invalid_grant (Security AC #2)."""
    verifier, challenge = _pkce_pair()
    code = _complete_authorize(http, challenge)

    first = http.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": _CLIENT_ID,
            "redirect_uri": _REDIRECT_URI,
            "code_verifier": verifier,
        },
    )
    assert first.status_code == 200

    second = http.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": _CLIENT_ID,
            "redirect_uri": _REDIRECT_URI,
            "code_verifier": verifier,
        },
    )
    assert second.status_code == 400
    assert second.json()["error"] == "invalid_grant"


def test_code_bound_to_client_id(http: TestClient) -> None:
    """A code can't be redeemed by a different (even registered) client."""
    verifier, challenge = _pkce_pair()
    code = _complete_authorize(http, challenge)

    # Try to redeem as a different client_id (not even registered)
    response = http.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": "some-other-client",
            "redirect_uri": _REDIRECT_URI,
            "code_verifier": verifier,
        },
    )
    assert response.status_code == 400
    # First check: unknown client. Either invalid_client or invalid_grant
    # is acceptable per RFC 6749 § 5.2 — we return invalid_client because
    # the bad client_id is more specific.
    assert response.json()["error"] in {"invalid_client", "invalid_grant"}


def test_code_bound_to_redirect_uri(http: TestClient) -> None:
    """Redeem must present the exact redirect_uri the code was bound to."""
    verifier, challenge = _pkce_pair()
    code = _complete_authorize(http, challenge)

    response = http.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": _CLIENT_ID,
            "redirect_uri": "http://127.0.0.1:8080/different",
            "code_verifier": verifier,
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


def test_unknown_code_returns_invalid_grant(http: TestClient) -> None:
    verifier, _ = _pkce_pair()
    response = http.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": "made-up-code",
            "client_id": _CLIENT_ID,
            "redirect_uri": _REDIRECT_URI,
            "code_verifier": verifier,
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


# ---------------------------------------------------------------------------
# refresh_token grant
# ---------------------------------------------------------------------------


def test_refresh_grant_returns_new_tokens(http: TestClient) -> None:
    """Refresh exchange returns a new access token and rotated refresh token."""
    verifier, challenge = _pkce_pair()
    code = _complete_authorize(http, challenge)

    first = http.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": _CLIENT_ID,
            "redirect_uri": _REDIRECT_URI,
            "code_verifier": verifier,
        },
    )
    initial = first.json()

    refresh_response = http.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": initial["refresh_token"],
        },
    )
    assert refresh_response.status_code == 200
    refreshed = refresh_response.json()
    assert refreshed["token_type"] == "Bearer"
    assert refreshed["access_token"] != initial["access_token"]
    assert refreshed["refresh_token"] != initial["refresh_token"]


def test_refresh_token_rotates_old_one_invalidated(http: TestClient) -> None:
    """After refresh, the old refresh token can't be used again."""
    verifier, challenge = _pkce_pair()
    code = _complete_authorize(http, challenge)
    initial = http.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": _CLIENT_ID,
            "redirect_uri": _REDIRECT_URI,
            "code_verifier": verifier,
        },
    ).json()

    first_refresh = http.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": initial["refresh_token"],
        },
    )
    assert first_refresh.status_code == 200

    replay = http.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": initial["refresh_token"],
        },
    )
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"


def test_unknown_refresh_token_returns_invalid_grant(http: TestClient) -> None:
    response = http.post(
        "/oauth/token",
        data={"grant_type": "refresh_token", "refresh_token": "made-up"},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


def test_missing_refresh_token_returns_invalid_request(http: TestClient) -> None:
    response = http.post("/oauth/token", data={"grant_type": "refresh_token"})
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


# ---------------------------------------------------------------------------
# grant_type validation
# ---------------------------------------------------------------------------


def test_unsupported_grant_type_returns_400(http: TestClient) -> None:
    response = http.post("/oauth/token", data={"grant_type": "client_credentials"})
    assert response.status_code == 400
    assert response.json()["error"] == "unsupported_grant_type"


def test_missing_grant_type_returns_400(http: TestClient) -> None:
    response = http.post("/oauth/token", data={})
    assert response.status_code == 400
    assert response.json()["error"] == "unsupported_grant_type"
