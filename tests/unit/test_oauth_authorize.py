"""Tests for /oauth/authorize + /_mock_idp/login (KAN-50)."""

from __future__ import annotations

import urllib.parse
from collections.abc import Iterator

import pytest
from starlette.testclient import TestClient

from vividscripts_mcp.oauth.codes import (
    AuthCode,
    AuthRequestState,
    MockAuthCodeStore,
    MockAuthRequestStateStore,
)
from vividscripts_mcp.oauth.session import MockSessionStore
from vividscripts_mcp.oauth.store import MockClientStore, RegisteredClient
from vividscripts_mcp.server import build_app

# A registered client used across the happy-path tests. PKCE-public client,
# redirect_uri pinned to a loopback callback Claude Code would use.
_REDIRECT_URI = "http://127.0.0.1:8080/callback"
_CLIENT_ID = "test-client"


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
def http(
    client_store: MockClientStore,
    session_store: MockSessionStore,
    request_state_store: MockAuthRequestStateStore,
    code_store: MockAuthCodeStore,
) -> Iterator[TestClient]:
    with TestClient(
        build_app(
            client_store=client_store,
            session_store=session_store,
            request_state_store=request_state_store,
            code_store=code_store,
        )
    ) as client:
        yield client


def _valid_query() -> dict[str, str]:
    return {
        "response_type": "code",
        "client_id": _CLIENT_ID,
        "redirect_uri": _REDIRECT_URI,
        "code_challenge": "abc_pkce_challenge_value",
        "code_challenge_method": "S256",
        "state": "csrf-nonce-xyz",
    }


# ---------------------------------------------------------------------------
# /oauth/authorize validation
# ---------------------------------------------------------------------------


def test_happy_path_redirects_to_mock_idp(http: TestClient) -> None:
    """A valid request 302s to /_mock_idp/login carrying a request_id."""
    response = http.get("/oauth/authorize", params=_valid_query(), follow_redirects=False)
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("/_mock_idp/login?request_id=")


def test_missing_client_id_returns_400(http: TestClient) -> None:
    params = _valid_query()
    del params["client_id"]
    response = http.get("/oauth/authorize", params=params, follow_redirects=False)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


def test_unknown_client_id_returns_400(http: TestClient) -> None:
    params = _valid_query()
    params["client_id"] = "not-registered"
    response = http.get("/oauth/authorize", params=params, follow_redirects=False)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_client"


def test_missing_redirect_uri_returns_400(http: TestClient) -> None:
    params = _valid_query()
    del params["redirect_uri"]
    response = http.get("/oauth/authorize", params=params, follow_redirects=False)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


def test_mismatched_redirect_uri_returns_400_no_redirect(http: TestClient) -> None:
    """Exact-match enforcement — never redirect to an unregistered URI."""
    params = _valid_query()
    params["redirect_uri"] = "http://127.0.0.1:8080/CALLBACK"  # case-different
    response = http.get("/oauth/authorize", params=params, follow_redirects=False)
    assert response.status_code == 400
    assert "redirect_uri" in response.json()["error_description"]


def test_unsupported_response_type_returns_400(http: TestClient) -> None:
    params = _valid_query()
    params["response_type"] = "token"
    response = http.get("/oauth/authorize", params=params, follow_redirects=False)
    assert response.status_code == 400
    assert response.json()["error"] == "unsupported_response_type"


def test_missing_pkce_returns_400(http: TestClient) -> None:
    """Security AC #1: PKCE is mandatory, no fallback."""
    params = _valid_query()
    del params["code_challenge"]
    response = http.get("/oauth/authorize", params=params, follow_redirects=False)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"
    assert "PKCE" in response.json()["error_description"]


def test_pkce_method_plain_rejected(http: TestClient) -> None:
    """``plain`` is explicitly refused — only S256 is allowed."""
    params = _valid_query()
    params["code_challenge_method"] = "plain"
    response = http.get("/oauth/authorize", params=params, follow_redirects=False)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"
    assert "S256" in response.json()["error_description"]


def test_pkce_method_missing_rejected(http: TestClient) -> None:
    params = _valid_query()
    del params["code_challenge_method"]
    response = http.get("/oauth/authorize", params=params, follow_redirects=False)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


def test_authorize_stores_pending_request(
    http: TestClient,
    request_state_store: MockAuthRequestStateStore,
) -> None:
    """The validated state is persisted under the request_id."""
    response = http.get("/oauth/authorize", params=_valid_query(), follow_redirects=False)
    location = response.headers["location"]
    parsed = urllib.parse.urlparse(location)
    request_id = urllib.parse.parse_qs(parsed.query)["request_id"][0]

    # consume() pops, so we have to peek differently — just call it and
    # confirm a state was stored.
    state = request_state_store.consume(request_id)
    assert state is not None
    assert state.client_id == _CLIENT_ID
    assert state.redirect_uri == _REDIRECT_URI
    assert state.code_challenge == "abc_pkce_challenge_value"
    assert state.code_challenge_method == "S256"
    assert state.state == "csrf-nonce-xyz"


# ---------------------------------------------------------------------------
# /_mock_idp/login
# ---------------------------------------------------------------------------


def test_mock_idp_get_renders_login_form(http: TestClient) -> None:
    response = http.get("/_mock_idp/login", params={"request_id": "any"})
    assert response.status_code == 200
    assert "user_id" in response.text


def test_mock_idp_login_completes_flow_with_code(
    http: TestClient,
    request_state_store: MockAuthRequestStateStore,
    code_store: MockAuthCodeStore,
) -> None:
    """End-to-end: authorize → mock login → code redirected to client."""
    auth_response = http.get("/oauth/authorize", params=_valid_query(), follow_redirects=False)
    location = auth_response.headers["location"]
    request_id = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)["request_id"][0]

    login_response = http.post(
        "/_mock_idp/login",
        data={"request_id": request_id, "user_id": "user-alpha"},
        follow_redirects=False,
    )
    assert login_response.status_code == 302
    redirect_url = login_response.headers["location"]
    assert redirect_url.startswith(_REDIRECT_URI + "?")

    qs = urllib.parse.parse_qs(urllib.parse.urlparse(redirect_url).query)
    assert "code" in qs
    assert qs["state"] == ["csrf-nonce-xyz"]
    issued_code = qs["code"][0]

    stored = code_store.consume(issued_code)
    assert stored is not None
    assert stored.client_id == _CLIENT_ID
    assert stored.user_id == "user-alpha"
    assert stored.redirect_uri == _REDIRECT_URI
    assert stored.code_challenge == "abc_pkce_challenge_value"

    # Second consume returns None (single-use).
    assert code_store.consume(issued_code) is None


def test_mock_idp_login_sets_session_cookie(
    http: TestClient,
    session_store: MockSessionStore,
) -> None:
    """Login establishes a session, enabling subsequent DCR on the same UA."""
    auth_response = http.get("/oauth/authorize", params=_valid_query(), follow_redirects=False)
    request_id = urllib.parse.parse_qs(
        urllib.parse.urlparse(auth_response.headers["location"]).query
    )["request_id"][0]

    login_response = http.post(
        "/_mock_idp/login",
        data={"request_id": request_id, "user_id": "user-alpha"},
        follow_redirects=False,
    )
    set_cookie = login_response.headers.get("set-cookie", "")
    assert "vs_session=" in set_cookie
    assert "HttpOnly" in set_cookie or "httponly" in set_cookie.lower()


def test_mock_idp_login_rejects_unknown_request_id(http: TestClient) -> None:
    response = http.post(
        "/_mock_idp/login",
        data={"request_id": "does-not-exist", "user_id": "user-alpha"},
        follow_redirects=False,
    )
    assert response.status_code == 400


def test_mock_idp_login_consumes_request_state_single_use(
    http: TestClient,
    request_state_store: MockAuthRequestStateStore,
) -> None:
    """A request_id can only be redeemed once."""
    auth_response = http.get("/oauth/authorize", params=_valid_query(), follow_redirects=False)
    request_id = urllib.parse.parse_qs(
        urllib.parse.urlparse(auth_response.headers["location"]).query
    )["request_id"][0]

    first = http.post(
        "/_mock_idp/login",
        data={"request_id": request_id, "user_id": "user-alpha"},
        follow_redirects=False,
    )
    assert first.status_code == 302

    second = http.post(
        "/_mock_idp/login",
        data={"request_id": request_id, "user_id": "user-alpha"},
        follow_redirects=False,
    )
    assert second.status_code == 400


# ---------------------------------------------------------------------------
# AuthCodeStore expiry semantics (also covered via integration, here for clarity)
# ---------------------------------------------------------------------------


def test_authcode_expiry_is_enforced() -> None:
    """An expired auth code returns None on consume."""
    store = MockAuthCodeStore()
    store.add(
        AuthCode(
            code="x",
            client_id=_CLIENT_ID,
            redirect_uri=_REDIRECT_URI,
            code_challenge="c",
            code_challenge_method="S256",
            scope=None,
            user_id="user-alpha",
            expires_at=0,  # epoch — long gone
        )
    )
    assert store.consume("x") is None


def test_authrequeststate_expiry_is_enforced() -> None:
    store = MockAuthRequestStateStore()
    store.add(
        AuthRequestState(
            request_id="r",
            client_id=_CLIENT_ID,
            redirect_uri=_REDIRECT_URI,
            state=None,
            code_challenge="c",
            code_challenge_method="S256",
            scope=None,
            expires_at=0,
        )
    )
    assert store.consume("r") is None
