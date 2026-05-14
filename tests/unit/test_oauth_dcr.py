"""Tests for the Dynamic Client Registration endpoint (KAN-49 / RFC 7591)."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

import pytest
from starlette.testclient import TestClient

from vividscripts_mcp.oauth.session import (
    SESSION_COOKIE_NAME,
    MockSessionStore,
    SessionInfo,
)
from vividscripts_mcp.oauth.store import MockClientStore
from vividscripts_mcp.server import build_app


@pytest.fixture
def client_store() -> MockClientStore:
    return MockClientStore()


@pytest.fixture
def session_store() -> MockSessionStore:
    return MockSessionStore()


@pytest.fixture
def authed_session(session_store: MockSessionStore) -> SessionInfo:
    return session_store.create(user_id="user-alpha")


@pytest.fixture
def http(
    client_store: MockClientStore,
    session_store: MockSessionStore,
) -> Iterator[TestClient]:
    with TestClient(build_app(client_store=client_store, session_store=session_store)) as client:
        yield client


def test_unauthenticated_registration_returns_401(http: TestClient) -> None:
    """Without a session cookie, registration is rejected with 401."""
    response = http.post(
        "/oauth/register",
        json={"redirect_uris": ["http://127.0.0.1:8080/callback"]},
    )
    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate", "").startswith("Session ")
    body = response.json()
    assert body["error"] == "unauthorized"


def test_invalid_session_cookie_returns_401(http: TestClient) -> None:
    """A cookie that doesn't match a known session is treated as no session."""
    http.cookies.set(SESSION_COOKIE_NAME, "not-a-real-session-id")
    response = http.post(
        "/oauth/register",
        json={"redirect_uris": ["http://127.0.0.1:8080/callback"]},
    )
    assert response.status_code == 401


def test_authenticated_registration_returns_201(
    http: TestClient,
    authed_session: SessionInfo,
) -> None:
    """Happy path: session cookie present, valid metadata, returns RFC 7591 response."""
    http.cookies.set(SESSION_COOKIE_NAME, authed_session.session_id)
    response = http.post(
        "/oauth/register",
        json={
            "redirect_uris": ["http://127.0.0.1:8080/callback"],
            "client_name": "Claude Code",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert isinstance(body["client_id"], str) and len(body["client_id"]) >= 16
    assert body["client_id_issued_at"] > 0
    assert body["redirect_uris"] == ["http://127.0.0.1:8080/callback"]
    assert body["client_name"] == "Claude Code"
    assert body["token_endpoint_auth_method"] == "none"
    assert body["grant_types"] == ["authorization_code", "refresh_token"]
    assert body["response_types"] == ["code"]


def test_https_redirect_uri_allowed(
    http: TestClient,
    authed_session: SessionInfo,
) -> None:
    """RFC 8252 permits HTTPS redirect URIs."""
    http.cookies.set(SESSION_COOKIE_NAME, authed_session.session_id)
    response = http.post(
        "/oauth/register",
        json={"redirect_uris": ["https://claude.ai/oauth/callback"]},
    )
    assert response.status_code == 201


def test_plain_http_redirect_uri_rejected(
    http: TestClient,
    authed_session: SessionInfo,
) -> None:
    """Public-web HTTP URIs are rejected (token-leak risk)."""
    http.cookies.set(SESSION_COOKIE_NAME, authed_session.session_id)
    response = http.post(
        "/oauth/register",
        json={"redirect_uris": ["http://evil.example.com/callback"]},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_redirect_uri"


def test_ipv6_loopback_redirect_uri_allowed(
    http: TestClient,
    authed_session: SessionInfo,
) -> None:
    """IPv6 loopback (RFC 8252) is acceptable for native clients."""
    http.cookies.set(SESSION_COOKIE_NAME, authed_session.session_id)
    response = http.post(
        "/oauth/register",
        json={"redirect_uris": ["http://[::1]:8080/cb"]},
    )
    assert response.status_code == 201


def test_unsupported_grant_type_rejected(
    http: TestClient,
    authed_session: SessionInfo,
) -> None:
    """Grant types outside the allow-list are rejected, not silently ignored."""
    http.cookies.set(SESSION_COOKIE_NAME, authed_session.session_id)
    response = http.post(
        "/oauth/register",
        json={
            "redirect_uris": ["http://127.0.0.1:8080/cb"],
            "grant_types": ["client_credentials"],
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_client_metadata"


def test_unsupported_response_type_rejected(
    http: TestClient,
    authed_session: SessionInfo,
) -> None:
    http.cookies.set(SESSION_COOKIE_NAME, authed_session.session_id)
    response = http.post(
        "/oauth/register",
        json={
            "redirect_uris": ["http://127.0.0.1:8080/cb"],
            "response_types": ["token"],  # implicit flow — not allowed
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_client_metadata"


def test_unsupported_auth_method_rejected(
    http: TestClient,
    authed_session: SessionInfo,
) -> None:
    http.cookies.set(SESSION_COOKIE_NAME, authed_session.session_id)
    response = http.post(
        "/oauth/register",
        json={
            "redirect_uris": ["http://127.0.0.1:8080/cb"],
            "token_endpoint_auth_method": "private_key_jwt",
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_client_metadata"


def test_missing_redirect_uris_rejected(
    http: TestClient,
    authed_session: SessionInfo,
) -> None:
    """redirect_uris is required per RFC 7591."""
    http.cookies.set(SESSION_COOKIE_NAME, authed_session.session_id)
    response = http.post("/oauth/register", json={})
    assert response.status_code == 400


def test_invalid_json_rejected(
    http: TestClient,
    authed_session: SessionInfo,
) -> None:
    """A body that isn't JSON yields a clean 400, not a stack trace."""
    http.cookies.set(SESSION_COOKIE_NAME, authed_session.session_id)
    response = http.post(
        "/oauth/register",
        content=b"this is not json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400


def test_client_persists_in_store_with_session_owner(
    http: TestClient,
    authed_session: SessionInfo,
    client_store: MockClientStore,
) -> None:
    """Registered clients are persisted with the registering user as owner."""
    http.cookies.set(SESSION_COOKIE_NAME, authed_session.session_id)
    response = http.post(
        "/oauth/register",
        json={"redirect_uris": ["http://localhost:9000/cb"]},
    )
    client_id = response.json()["client_id"]

    stored = client_store.get(client_id)
    assert stored is not None
    assert stored.owner_user_id == "user-alpha"
    assert stored.redirect_uris == ("http://localhost:9000/cb",)


def test_audit_event_emitted_on_registration(
    http: TestClient,
    authed_session: SessionInfo,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Every successful registration emits a structured audit event."""
    caplog.set_level(logging.INFO, logger="vividscripts_mcp.audit")
    http.cookies.set(SESSION_COOKIE_NAME, authed_session.session_id)
    response = http.post(
        "/oauth/register",
        json={"redirect_uris": ["http://127.0.0.1:8080/cb"]},
    )
    assert response.status_code == 201

    audit_records = [r for r in caplog.records if r.name == "vividscripts_mcp.audit"]
    assert len(audit_records) == 1
    event = json.loads(audit_records[0].message)
    assert event["event"] == "oauth.client.registered"
    assert event["owner_user_id"] == "user-alpha"
    assert event["client_id"] == response.json()["client_id"]
