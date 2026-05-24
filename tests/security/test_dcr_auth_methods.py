"""Audit finding #7 — DCR ``token_endpoint_auth_method`` honesty.

DCR previously echoed ``client_secret_basic`` / ``client_secret_post`` as
accepted ``token_endpoint_auth_method`` values, but never issued a
``client_secret`` and never verified one at ``/oauth/token``. The server
was advertising confidential-client semantics it did not implement —
"misrepresentation of capability" in audit terms.

Resolution per the ticket (Phase-1/2 honesty): restrict the DCR
allow-list to ``{"none"}`` to match the authorization-server metadata
document (which already advertises only ``["none"]``). When the package
gains real confidential-client support later, the allow-list opens up
alongside actual secret issuance + verification, not before.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from starlette.testclient import TestClient

from vividscripts_mcp.oauth.session import SESSION_COOKIE_NAME, MockSessionStore
from vividscripts_mcp.oauth.store import MockClientStore
from vividscripts_mcp.server import build_app


@pytest.fixture
def session_store() -> MockSessionStore:
    return MockSessionStore()


@pytest.fixture
def client_store() -> MockClientStore:
    return MockClientStore()


@pytest.fixture
def http(session_store: MockSessionStore, client_store: MockClientStore) -> Iterator[TestClient]:
    with TestClient(build_app(session_store=session_store, client_store=client_store)) as client:
        yield client


@pytest.fixture
def authed(http: TestClient, session_store: MockSessionStore) -> None:
    session = session_store.create(user_id="user-alpha")
    http.cookies.set(SESSION_COOKIE_NAME, session.session_id)


@pytest.mark.parametrize("auth_method", ["client_secret_basic", "client_secret_post"])
def test_dcr_rejects_secret_based_auth_methods(
    http: TestClient, authed: None, auth_method: str
) -> None:
    response = http.post(
        "/oauth/register",
        json={
            "redirect_uris": ["http://127.0.0.1:8080/cb"],
            "token_endpoint_auth_method": auth_method,
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_client_metadata"


def test_dcr_accepts_none(http: TestClient, authed: None) -> None:
    response = http.post(
        "/oauth/register",
        json={
            "redirect_uris": ["http://127.0.0.1:8080/cb"],
            "token_endpoint_auth_method": "none",
        },
    )
    assert response.status_code == 201
    assert response.json()["token_endpoint_auth_method"] == "none"
