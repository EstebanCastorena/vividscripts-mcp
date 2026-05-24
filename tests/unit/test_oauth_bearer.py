"""Tests for the Bearer token validator (KAN-52 / RFC 6750)."""

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

from vividscripts_mcp.oauth.bearer import (
    InProcessJWKSProvider,
    UserClaims,
    redact_token,
    validate_bearer_token,
)
from vividscripts_mcp.oauth.codes import MockAuthCodeStore, MockAuthRequestStateStore
from vividscripts_mcp.oauth.keys import ALGORITHM, KID, get_signing_key, reset_signing_key
from vividscripts_mcp.oauth.session import MockSessionStore
from vividscripts_mcp.oauth.store import MockClientStore, RegisteredClient
from vividscripts_mcp.oauth.tokens import (
    DEFAULT_AUDIENCE,
    DEFAULT_ISSUER,
    MockRefreshTokenStore,
    mint_access_token,
)
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
def http(client_store: MockClientStore, stores: dict[str, Any]) -> Iterator[TestClient]:
    with TestClient(build_app(client_store=client_store, **stores)) as client:
        yield client


def _mint_test_token(
    *,
    user_id: str = "user-alpha",
    client_id: str = _CLIENT_ID,
    scope: str | None = None,
    issuer: str = DEFAULT_ISSUER,
    audience: str = DEFAULT_AUDIENCE,
    ttl: int = 3600,
) -> str:
    token, _ = mint_access_token(
        user_id=user_id,
        client_id=client_id,
        scope=scope,
        issuer=issuer,
        audience=audience,
        ttl_seconds=ttl,
    )
    return token


# ---------------------------------------------------------------------------
# validate_bearer_token: unit-level
# ---------------------------------------------------------------------------


def test_valid_token_returns_user_claims() -> None:
    token = _mint_test_token(scope="openid profile")
    claims = validate_bearer_token(token, InProcessJWKSProvider())
    assert claims is not None
    assert isinstance(claims, UserClaims)
    assert claims.sub == "user-alpha"
    assert claims.client_id == _CLIENT_ID
    assert claims.scope == "openid profile"
    assert claims.exp > claims.iat


def test_wrong_audience_rejected() -> None:
    token = _mint_test_token(audience="https://attacker.example.com")
    assert validate_bearer_token(token, InProcessJWKSProvider()) is None


def test_wrong_issuer_rejected() -> None:
    token = _mint_test_token(issuer="https://evil.example.com")
    assert validate_bearer_token(token, InProcessJWKSProvider()) is None


def test_expired_token_rejected() -> None:
    """A token with exp in the past validates to None."""
    # Mint with a 1-second TTL then sleep past it.
    token = _mint_test_token(ttl=1)
    time.sleep(2)
    assert validate_bearer_token(token, InProcessJWKSProvider()) is None


def test_hs256_token_rejected() -> None:
    """An HS256-signed token must NOT be accepted (Security AC #4)."""
    now = int(time.time())
    forged = jwt.encode(
        {
            "iss": DEFAULT_ISSUER,
            "aud": DEFAULT_AUDIENCE,
            "sub": "user-alpha",
            "client_id": _CLIENT_ID,
            "iat": now,
            "exp": now + 3600,
            "token_use": "access",
            "jti": "x",
        },
        key="any-shared-secret",
        algorithm="HS256",
        headers={"kid": KID},
    )
    assert validate_bearer_token(forged, InProcessJWKSProvider()) is None


def test_none_algorithm_rejected() -> None:
    """An 'alg: none' token must be rejected."""
    now = int(time.time())
    payload = {
        "iss": DEFAULT_ISSUER,
        "aud": DEFAULT_AUDIENCE,
        "sub": "user-alpha",
        "client_id": _CLIENT_ID,
        "iat": now,
        "exp": now + 3600,
        "token_use": "access",
        "jti": "x",
    }
    # Hand-construct an unsigned JWT
    header = (
        base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT","kid":"' + KID.encode() + b'"}')
        .rstrip(b"=")
        .decode("ascii")
    )
    body = (
        base64.urlsafe_b64encode(jwt.encode(payload, "k", algorithm="HS256").split(".")[1].encode())
        .rstrip(b"=")
        .decode("ascii")
    )
    forged = f"{header}.{body}."
    assert validate_bearer_token(forged, InProcessJWKSProvider()) is None


def test_unknown_kid_rejected() -> None:
    """A token with an unknown ``kid`` returns None."""
    # Mint with the real key but tamper the header to claim a different kid.
    token = _mint_test_token()
    parts = token.split(".")
    # decode + rewrite kid
    pad = "=" * ((4 - len(parts[0]) % 4) % 4)
    import json

    header = json.loads(base64.urlsafe_b64decode(parts[0] + pad))
    header["kid"] = "unknown-kid"
    new_header = base64.urlsafe_b64encode(json.dumps(header).encode()).rstrip(b"=").decode("ascii")
    tampered = f"{new_header}.{parts[1]}.{parts[2]}"
    assert validate_bearer_token(tampered, InProcessJWKSProvider()) is None


def test_token_use_not_access_rejected() -> None:
    """Tokens with token_use != 'access' (e.g., refresh, id) are refused."""
    now = int(time.time())
    key = get_signing_key()
    forged = jwt.encode(
        {
            "iss": DEFAULT_ISSUER,
            "aud": DEFAULT_AUDIENCE,
            "sub": "user-alpha",
            "client_id": _CLIENT_ID,
            "iat": now,
            "exp": now + 3600,
            "token_use": "refresh",  # wrong type
            "jti": "x",
        },
        key.private_pem,
        algorithm=ALGORITHM,
        headers={"kid": KID},
    )
    assert validate_bearer_token(forged, InProcessJWKSProvider()) is None


def test_malformed_token_rejected() -> None:
    assert validate_bearer_token("not.a.jwt", InProcessJWKSProvider()) is None
    assert validate_bearer_token("garbage", InProcessJWKSProvider()) is None
    assert validate_bearer_token("", InProcessJWKSProvider()) is None


# ---------------------------------------------------------------------------
# /mcp middleware enforcement (integration through TestClient)
# ---------------------------------------------------------------------------


def test_mcp_unauthenticated_returns_401_with_prm_pointer(http: TestClient) -> None:
    response = http.get("/mcp")
    assert response.status_code == 401
    challenge = response.headers["WWW-Authenticate"]
    assert challenge.startswith("Bearer ")
    assert "resource_metadata=" in challenge


def test_mcp_invalid_token_returns_401_with_error_invalid_token(http: TestClient) -> None:
    """Bad token surfaces error=\"invalid_token\" in WWW-Authenticate."""
    response = http.get(
        "/mcp",
        headers={"Authorization": "Bearer this.is.not.real"},
    )
    assert response.status_code == 401
    assert 'error="invalid_token"' in response.headers["WWW-Authenticate"]


def test_mcp_valid_token_passes_through_middleware(http: TestClient) -> None:
    """A valid token reaches the inner MCP transport (not 401)."""
    token = _mint_test_token()
    response = http.get("/mcp", headers={"Authorization": f"Bearer {token}"})
    # Inner FastMCP will reject the GET shape with 4xx or 406 — point is
    # we got past the auth gate, NOT a 401 from the middleware.
    assert response.status_code != 401


# ---------------------------------------------------------------------------
# /.well-known/jwks.json
# ---------------------------------------------------------------------------


def test_jwks_endpoint_serves_public_key(http: TestClient) -> None:
    response = http.get("/.well-known/jwks.json")
    assert response.status_code == 200
    body = response.json()
    assert "keys" in body
    assert len(body["keys"]) == 1
    jwk = body["keys"][0]
    assert jwk["kid"] == KID
    assert jwk["alg"] == ALGORITHM
    assert jwk["kty"] == "RSA"
    assert "n" in jwk
    assert "e" in jwk


def test_jwks_endpoint_unauthenticated_allowed(http: TestClient) -> None:
    """JWKS must be reachable without a Bearer token — clients use it BEFORE
    they have one."""
    response = http.get("/.well-known/jwks.json")  # no Authorization header
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# redact_token
# ---------------------------------------------------------------------------


def test_redact_token_uses_jti_when_available() -> None:
    claims = UserClaims(
        sub="user-alpha", client_id="c", scope=None, jti="abc-xyz", exp=9999999999, iat=1
    )
    assert redact_token("anything-here", claims=claims) == "jti:abc-xyz"


def test_redact_token_falls_back_to_sha256_prefix() -> None:
    redacted = redact_token("some-token", claims=None)
    assert redacted.startswith("sha256:")
    assert "some-token" not in redacted


def test_redact_token_never_includes_raw_token() -> None:
    """The raw token must never appear in the redacted form."""
    raw = "the-secret-bearer-token"
    redacted = redact_token(raw)
    assert raw not in redacted


# ---------------------------------------------------------------------------
# /oauth/token issued tokens validate end-to-end
# ---------------------------------------------------------------------------


def test_token_issued_by_oauth_token_endpoint_validates(http: TestClient) -> None:
    """A token issued by /oauth/token validates through the Bearer validator."""
    verifier, challenge = _pkce_pair()
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
    code = urllib.parse.parse_qs(urllib.parse.urlparse(login_response.headers["location"]).query)[
        "code"
    ][0]

    token_response = http.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": _CLIENT_ID,
            "redirect_uri": _REDIRECT_URI,
            "code_verifier": verifier,
        },
    )
    access_token = token_response.json()["access_token"]

    claims = validate_bearer_token(access_token, InProcessJWKSProvider())
    assert claims is not None
    assert claims.sub == "user-alpha"
    assert claims.client_id == _CLIENT_ID
