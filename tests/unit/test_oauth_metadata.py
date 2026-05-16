"""Tests for the OAuth Protected Resource Metadata endpoint (RFC 9728, KAN-48)."""

from __future__ import annotations

from starlette.testclient import TestClient

from vividscripts_mcp.oauth.metadata import (
    PRM_PATH,
    ProtectedResourceMetadata,
    build_prm_payload,
)
from vividscripts_mcp.server import build_app


def test_prm_endpoint_returns_required_rfc_9728_fields() -> None:
    """GET /.well-known/oauth-protected-resource returns the RFC 9728 payload."""
    with TestClient(build_app()) as client:
        response = client.get(PRM_PATH)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")

    payload = response.json()

    # Required-by-ticket fields (RFC 9728 § 3)
    assert payload["resource"] == "https://app.vividscripts.com/mcp"
    assert payload["authorization_servers"] == ["https://app.vividscripts.com"]
    assert payload["bearer_methods_supported"] == ["header"]
    assert payload["resource_documentation"].startswith("https://")

    # Optional-but-included fields
    assert payload["scopes_supported"] == ["openid", "profile", "email"]
    assert payload["resource_signing_alg_values_supported"] == ["RS256"]


def test_prm_payload_round_trips_through_pydantic_model() -> None:
    """Validates the payload against the RFC 9728 schema declared in metadata.py.

    ``ConfigDict(extra="forbid")`` would reject any unexpected keys, so this
    round-trip is the schema-level guard the ticket asks for.
    """
    payload = build_prm_payload().model_dump()
    parsed = ProtectedResourceMetadata.model_validate(payload)
    assert parsed.model_dump() == payload


def test_unauthed_mcp_returns_401_with_www_authenticate() -> None:
    """A naked request to /mcp earns 401 + WWW-Authenticate pointing at the PRM."""
    with TestClient(build_app()) as client:
        response = client.get("/mcp")

    assert response.status_code == 401

    challenge = response.headers["WWW-Authenticate"]
    # RFC 6750 § 3 + RFC 9728 § 5.1 — Bearer challenge with resource_metadata param
    assert challenge.startswith("Bearer resource_metadata=")
    assert PRM_PATH in challenge
    # The metadata URL is built from the request's own host so the client can
    # fetch it back (TestClient uses http://testserver as the synthetic host).
    assert "testserver" in challenge


def test_www_authenticate_honors_x_forwarded_proto() -> None:
    """Behind a TLS-terminating proxy the metadata URL must stay https.

    TestClient speaks http; an ``X-Forwarded-Proto: https`` header (set by
    CloudFront/ALB in production) must make the advertised
    resource_metadata URL https, not http.
    """
    with TestClient(build_app()) as client:
        response = client.get("/mcp", headers={"X-Forwarded-Proto": "https"})

    assert response.status_code == 401
    challenge = response.headers["WWW-Authenticate"]
    assert 'resource_metadata="https://' in challenge
    assert "http://testserver" not in challenge


def test_bearer_present_mcp_does_not_401_from_middleware() -> None:
    """A request carrying a valid Bearer JWT passes the auth middleware.

    KAN-52 introduced real validation, so a placeholder token now correctly
    fails — the meaningful assertion is that a *valid* token reaches the
    inner MCP transport (which may then reject the request for unrelated
    protocol reasons like GET-vs-POST, but not with a 401 from this layer).
    """
    from vividscripts_mcp.oauth.tokens import mint_access_token

    token, _ = mint_access_token(user_id="user-alpha", client_id="some-client")
    with TestClient(build_app()) as client:
        response = client.get("/mcp", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code != 401
