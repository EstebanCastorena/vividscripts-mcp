"""Audit finding #11 — ``WWW-Authenticate: resource_metadata=...`` safety.

``BearerEnforcementMiddleware._metadata_url`` reflected the client's
``Host`` and ``X-Forwarded-Proto`` headers into the URL it advertises
for OAuth discovery. With no trusted-proxy allow-list, a MITM (or any
unauthenticated client) can steer a victim's discovery to an
attacker-controlled authorization server document — full AS phishing.

Fix shape: in **broker mode** (the production path — a ``CognitoConfig``
is passed to ``build_app``) the metadata URL is derived from
``cognito.public_base_url`` and the request's headers are ignored.
Offline mode (no ``cognito``) keeps deriving from the scope so dev/test
on ``http://testserver`` still works.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from starlette.testclient import TestClient

from vividscripts_mcp.oauth.cognito import CognitoConfig
from vividscripts_mcp.server import build_app


@pytest.fixture
def cognito() -> CognitoConfig:
    return CognitoConfig(
        issuer="https://cognito-idp.us-east-1.amazonaws.com/us-east-1_TESTPOOL",
        hosted_ui_domain="https://auth.vividscripts.test",
        client_id="test-client-id",
        client_secret="test-client-secret",
        public_base_url="https://app.vividscripts.test",
    )


@pytest.fixture
def broker_http(cognito: CognitoConfig) -> Iterator[TestClient]:
    with TestClient(build_app(cognito=cognito), base_url="http://127.0.0.1:8000") as client:
        yield client


def test_broker_metadata_url_ignores_forged_host(broker_http: TestClient) -> None:
    """A forged ``Host`` header must not appear in the advertised
    ``resource_metadata`` URL when a canonical base URL is configured."""
    response = broker_http.post(
        "/mcp",
        headers={"Host": "attacker.com"},
        json={"hello": "world"},
    )
    assert response.status_code == 401
    challenge = response.headers["WWW-Authenticate"]
    assert "attacker.com" not in challenge
    assert "app.vividscripts.test" in challenge


def test_broker_metadata_url_ignores_forged_x_forwarded_proto(
    broker_http: TestClient,
) -> None:
    """Even legitimate-looking ``X-Forwarded-Proto`` values must not
    override the canonical scheme in broker mode."""
    response = broker_http.post(
        "/mcp",
        headers={"X-Forwarded-Proto": "javascript"},
        json={"hello": "world"},
    )
    assert response.status_code == 401
    challenge = response.headers["WWW-Authenticate"]
    assert "javascript://" not in challenge
    assert "https://app.vividscripts.test" in challenge


def test_broker_metadata_url_ignores_x_forwarded_host(broker_http: TestClient) -> None:
    """The X-Forwarded-Host variant must also be ignored when a canonical
    base URL is configured — same phishing primitive, different header."""
    response = broker_http.post(
        "/mcp",
        headers={"X-Forwarded-Host": "phisher.example"},
        json={"hello": "world"},
    )
    assert response.status_code == 401
    challenge = response.headers["WWW-Authenticate"]
    assert "phisher.example" not in challenge
    assert "app.vividscripts.test" in challenge
