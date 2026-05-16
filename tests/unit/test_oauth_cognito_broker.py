"""Tests for the Cognito broker (KAN-85 / KAN-36 pass-through).

Covers the broker-mode (``build_app(cognito=...)``) surface:

- the Cognito Bearer matrix ported from KAN-64 (Cognito access tokens
  have **no** ``aud``; identity is the ``client_id`` claim),
- the authorize → callback → token broker flow with a stubbed Cognito
  token endpoint, asserting **pass-through** (the issued tokens are
  Cognito's, not self-minted),
- PKCE / single-use still enforced on the package's one-shot code,
- the offline mock IdP is not mounted in broker mode,
- PRM + RFC 8414 AS metadata advertise the real deployment.

Offline-mode behavior keeps its own suites (``test_oauth_*``); this
file only exercises the paths that the ``CognitoConfig`` flag enables.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
import urllib.parse
from collections.abc import Iterator
from typing import Any

import jwt
import pytest
from starlette.testclient import TestClient

from vividscripts_mcp.oauth import cognito as cognito_mod
from vividscripts_mcp.oauth.bearer import (
    InProcessJWKSProvider,
    UserClaims,
    validate_bearer_token,
)
from vividscripts_mcp.oauth.codes import MockAuthCodeStore, MockAuthRequestStateStore
from vividscripts_mcp.oauth.cognito import CognitoConfig, CognitoTokens
from vividscripts_mcp.oauth.dcr import BROKER_CLIENT_OWNER
from vividscripts_mcp.oauth.keys import ALGORITHM, KID, get_signing_key, reset_signing_key
from vividscripts_mcp.oauth.session import MockSessionStore
from vividscripts_mcp.oauth.store import MockClientStore, RegisteredClient
from vividscripts_mcp.oauth.tokens import MockRefreshTokenStore
from vividscripts_mcp.server import build_app

_REDIRECT_URI = "http://127.0.0.1:8080/callback"
_CLIENT_ID = "claude-code-client"
_COGNITO_CLIENT_ID = "cognito-app-client-id"
_ISSUER = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_TEST"


def _config() -> CognitoConfig:
    return CognitoConfig(
        issuer=_ISSUER,
        client_id=_COGNITO_CLIENT_ID,
        client_secret="super-secret",
        hosted_ui_domain="https://auth.vividscripts.ai/",
        public_base_url="https://vividscripts.ai/",
    )


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _registered_client() -> RegisteredClient:
    return RegisteredClient(
        client_id=_CLIENT_ID,
        issued_at=1_700_000_000,
        owner_user_id="cognito-sub-123",
        redirect_uris=(_REDIRECT_URI,),
        token_endpoint_auth_method="none",
        grant_types=("authorization_code", "refresh_token"),
        response_types=("code",),
        client_name="Claude Code",
    )


def _mint_cognito_token(
    *,
    sub: str = "cognito-sub-123",
    client_id: str = _COGNITO_CLIENT_ID,
    issuer: str = _ISSUER,
    token_use: str = "access",
    ttl: int = 3600,
    include_client_id: bool = True,
    algorithm: str = ALGORITHM,
    key: Any | None = None,
) -> str:
    """Mint a Cognito-**shaped** token: ``client_id`` + ``token_use``, no ``aud``."""
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": issuer,
        "sub": sub,
        "token_use": token_use,
        "scope": "openid profile email",
        "iat": now,
        "exp": now + ttl,
        "jti": secrets.token_urlsafe(8),
    }
    if include_client_id:
        claims["client_id"] = client_id
    signing_key = key if key is not None else get_signing_key().private_pem
    return jwt.encode(claims, signing_key, algorithm=algorithm, headers={"kid": KID})


@pytest.fixture(autouse=True)
def _fresh_key() -> Iterator[None]:
    reset_signing_key()
    yield
    reset_signing_key()


@pytest.fixture
def cognito() -> CognitoConfig:
    return _config()


@pytest.fixture
def client_store() -> MockClientStore:
    store = MockClientStore()
    store.add(_registered_client())
    return store


@pytest.fixture
def stores() -> dict[str, Any]:
    return {
        "session_store": MockSessionStore(),
        "request_state_store": MockAuthRequestStateStore(),
        "code_store": MockAuthCodeStore(),
        "refresh_token_store": MockRefreshTokenStore(),
    }


@pytest.fixture
def canned_tokens() -> CognitoTokens:
    """Tokens the stubbed Cognito endpoint returns. The access token is
    Cognito-shaped and signed with the in-process key so it also
    validates through the /mcp Bearer middleware."""
    return CognitoTokens(
        access_token=_mint_cognito_token(),
        refresh_token="cognito-refresh-token",
        id_token=None,
        expires_in=3600,
    )


@pytest.fixture
def http(
    cognito: CognitoConfig,
    client_store: MockClientStore,
    stores: dict[str, Any],
    canned_tokens: CognitoTokens,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    async def _fake_exchange(
        config: CognitoConfig, *, code: str, redirect_uri: str
    ) -> CognitoTokens:
        return canned_tokens

    async def _fake_refresh(config: CognitoConfig, *, refresh_token: str) -> CognitoTokens:
        return CognitoTokens(
            access_token=_mint_cognito_token(),
            refresh_token=None,  # Cognito doesn't rotate refresh tokens
            id_token=None,
            expires_in=3600,
        )

    monkeypatch.setattr(cognito_mod, "exchange_code", _fake_exchange)
    monkeypatch.setattr(cognito_mod, "refresh_tokens", _fake_refresh)

    app = build_app(
        client_store=client_store,
        jwks_provider=InProcessJWKSProvider(),
        cognito=cognito,
        **stores,
    )
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        yield client


# ---------------------------------------------------------------------------
# Cognito Bearer matrix (ported from KAN-64)
# ---------------------------------------------------------------------------


def _validate_cognito(token: str) -> UserClaims | None:
    return validate_bearer_token(
        token,
        InProcessJWKSProvider(),
        issuer=_ISSUER,
        audience=None,
        expected_client_id=_COGNITO_CLIENT_ID,
    )


def test_valid_cognito_access_token_passes() -> None:
    claims = _validate_cognito(_mint_cognito_token())
    assert claims is not None
    assert claims.sub == "cognito-sub-123"
    assert claims.client_id == _COGNITO_CLIENT_ID


def test_wrong_issuer_rejected() -> None:
    assert _validate_cognito(_mint_cognito_token(issuer="https://evil.example")) is None


def test_wrong_client_id_rejected() -> None:
    assert _validate_cognito(_mint_cognito_token(client_id="some-other-app")) is None


def test_missing_client_id_rejected() -> None:
    assert _validate_cognito(_mint_cognito_token(include_client_id=False)) is None


def test_id_token_rejected() -> None:
    """token_use=id (an ID token) must not work as a Bearer credential."""
    assert _validate_cognito(_mint_cognito_token(token_use="id")) is None


def test_expired_cognito_token_rejected() -> None:
    token = _mint_cognito_token(ttl=1)
    time.sleep(2)
    assert _validate_cognito(token) is None


def test_hs256_cognito_token_rejected() -> None:
    forged = _mint_cognito_token(algorithm="HS256", key="shared-secret-attacker")
    assert _validate_cognito(forged) is None


def test_no_aud_required_for_cognito() -> None:
    """A Cognito token with no ``aud`` is fine — proves verify_aud is off."""
    token = _mint_cognito_token()
    assert "aud" not in jwt.decode(token, options={"verify_signature": False})
    assert _validate_cognito(token) is not None


# ---------------------------------------------------------------------------
# Broker flow: authorize → callback → token (pass-through)
# ---------------------------------------------------------------------------


def _authorize(http: TestClient, challenge: str) -> tuple[str, str]:
    """Run /oauth/authorize; return (cognito_state, hosted_ui_location)."""
    response = http.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": _CLIENT_ID,
            "redirect_uri": _REDIRECT_URI,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "client-csrf",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    location = response.headers["location"]
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
    return qs["state"][0], location


def test_authorize_redirects_to_cognito_hosted_ui(http: TestClient) -> None:
    _, challenge = _pkce_pair()
    state, location = _authorize(http, challenge)
    assert location.startswith("https://auth.vividscripts.ai/oauth2/authorize?")
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
    assert qs["client_id"] == [_COGNITO_CLIENT_ID]
    assert qs["redirect_uri"] == ["https://vividscripts.ai/oauth/callback"]
    assert qs["response_type"] == ["code"]
    assert state  # the round-trip nonce (the pending request_id)


def test_full_broker_flow_passes_cognito_tokens_through(
    http: TestClient, canned_tokens: CognitoTokens
) -> None:
    verifier, challenge = _pkce_pair()
    state, _ = _authorize(http, challenge)

    callback = http.get(
        "/oauth/callback",
        params={"code": "cognito-auth-code", "state": state},
        follow_redirects=False,
    )
    assert callback.status_code == 302
    redirect = callback.headers["location"]
    assert redirect.startswith(_REDIRECT_URI + "?")
    cb_qs = urllib.parse.parse_qs(urllib.parse.urlparse(redirect).query)
    assert cb_qs["state"] == ["client-csrf"]  # client's original CSRF state
    package_code = cb_qs["code"][0]

    token_response = http.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": package_code,
            "client_id": _CLIENT_ID,
            "redirect_uri": _REDIRECT_URI,
            "code_verifier": verifier,
        },
    )
    assert token_response.status_code == 200
    body = token_response.json()
    # Pass-through: the issued tokens ARE Cognito's, not self-minted.
    assert body["access_token"] == canned_tokens.access_token
    assert body["refresh_token"] == canned_tokens.refresh_token
    assert body["token_type"] == "Bearer"

    # And that Cognito access token clears the /mcp Bearer middleware.
    mcp = http.get("/mcp", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert mcp.status_code != 401


def test_broker_pkce_mismatch_rejected(http: TestClient) -> None:
    _, challenge = _pkce_pair()
    state, _ = _authorize(http, challenge)
    callback = http.get(
        "/oauth/callback",
        params={"code": "cognito-auth-code", "state": state},
        follow_redirects=False,
    )
    package_code = urllib.parse.parse_qs(urllib.parse.urlparse(callback.headers["location"]).query)[
        "code"
    ][0]

    response = http.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": package_code,
            "client_id": _CLIENT_ID,
            "redirect_uri": _REDIRECT_URI,
            "code_verifier": "wrong-verifier",
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


def test_broker_package_code_is_single_use(http: TestClient) -> None:
    verifier, challenge = _pkce_pair()
    state, _ = _authorize(http, challenge)
    callback = http.get(
        "/oauth/callback",
        params={"code": "cognito-auth-code", "state": state},
        follow_redirects=False,
    )
    package_code = urllib.parse.parse_qs(urllib.parse.urlparse(callback.headers["location"]).query)[
        "code"
    ][0]
    data = {
        "grant_type": "authorization_code",
        "code": package_code,
        "client_id": _CLIENT_ID,
        "redirect_uri": _REDIRECT_URI,
        "code_verifier": verifier,
    }
    assert http.post("/oauth/token", data=data).status_code == 200
    replay = http.post("/oauth/token", data=data)
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"


def test_callback_rejects_unknown_state(http: TestClient) -> None:
    response = http.get(
        "/oauth/callback",
        params={"code": "x", "state": "never-issued"},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


def test_callback_forwards_cognito_error(http: TestClient) -> None:
    response = http.get(
        "/oauth/callback",
        params={"error": "access_denied"},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert response.json()["error"] == "access_denied"


def test_refresh_grant_proxies_to_cognito(http: TestClient) -> None:
    response = http.post(
        "/oauth/token",
        data={"grant_type": "refresh_token", "refresh_token": "any-cognito-refresh"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "Bearer"
    assert body["access_token"]
    # Cognito doesn't rotate; the client keeps reusing the presented token.
    assert body["refresh_token"] == "any-cognito-refresh"


# ---------------------------------------------------------------------------
# Broker-mode surface guarantees
# ---------------------------------------------------------------------------


def test_mock_idp_not_mounted_in_broker_mode(http: TestClient) -> None:
    response = http.get("/_mock_idp/login", params={"request_id": "x"})
    assert response.status_code != 302
    assert "Mock IdP" not in response.text


def test_broker_allowlists_production_host(cognito: CognitoConfig) -> None:
    """FastMCP's DNS-rebinding guard must allow the real deployment host
    (else production requests get HTTP 421); offline keeps localhost-only."""
    from vividscripts_mcp.adapters.mock import MockBackend
    from vividscripts_mcp.server import create_mcp_server

    broker = create_mcp_server(MockBackend(), cognito)
    ts = broker.settings.transport_security
    assert ts is not None
    assert ts.enable_dns_rebinding_protection is True
    assert "vividscripts.ai" in ts.allowed_hosts

    offline = create_mcp_server(MockBackend())
    ts_off = offline.settings.transport_security
    assert ts_off is None or "vividscripts.ai" not in ts_off.allowed_hosts


def test_broker_dcr_is_open_no_session_required(
    http: TestClient, client_store: MockClientStore
) -> None:
    """In broker mode DCR must succeed without a prior session cookie —
    Claude Code registers before any login (Cognito is the real gate)."""
    resp = http.post(
        "/oauth/register",
        json={
            "redirect_uris": ["http://127.0.0.1:8080/callback"],
            "client_name": "Claude Code",
        },
    )
    assert resp.status_code == 201
    client_id = resp.json()["client_id"]
    stored = client_store.get(client_id)
    assert stored is not None
    assert stored.owner_user_id == BROKER_CLIENT_OWNER


def test_prm_advertises_real_deployment(http: TestClient) -> None:
    prm = http.get("/.well-known/oauth-protected-resource").json()
    assert prm["resource"] == "https://vividscripts.ai/mcp"
    assert prm["authorization_servers"] == ["https://vividscripts.ai"]


def test_as_metadata_advertises_facade_endpoints(http: TestClient) -> None:
    meta = http.get("/.well-known/oauth-authorization-server")
    assert meta.status_code == 200
    body = meta.json()
    assert body["issuer"] == "https://vividscripts.ai"
    assert body["authorization_endpoint"] == "https://vividscripts.ai/oauth/authorize"
    assert body["token_endpoint"] == "https://vividscripts.ai/oauth/token"
    assert body["registration_endpoint"] == "https://vividscripts.ai/oauth/register"
    assert body["code_challenge_methods_supported"] == ["S256"]
