"""Audit finding #10 — ``mint_magic_link.ttl_seconds`` server-side clamp.

The docstring promises ≤5 min. Nothing enforced it. ``ttl_seconds=10**12``
silently produced a link valid for thirty-one thousand years, and a
negative value produced a link that was already expired before it was
returned. Both defeat the entire "short-lived handoff" premise.

Acceptable range: ``1 <= ttl_seconds <= 300``. Anything outside raises
a clear ``ValueError`` from the tool — no silent clamping (the caller
needs to see they passed something nonsensical).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from vividscripts_mcp.adapters.mock import MockBackend
from vividscripts_mcp.models import ProjectSettings
from vividscripts_mcp.oauth.bearer import UserClaims
from vividscripts_mcp.oauth.context import set_user_claims
from vividscripts_mcp.tools.handoff import make_mint_magic_link_tool


@pytest.fixture
def backend() -> MockBackend:
    return MockBackend(base_url="https://app.vividscripts.test")


@pytest.fixture
def project_id(backend: MockBackend) -> str:
    return backend.create_project(
        user_id="user-alpha", story="A short story.", settings=ProjectSettings()
    ).project_id


@pytest.fixture
def _auth() -> Iterator[None]:
    set_user_claims(
        UserClaims(
            sub="user-alpha",
            client_id="c",
            scope=None,
            jti="j-ttl",
            exp=9999999999,
            iat=1,
        )
    )
    yield
    set_user_claims(None)


@pytest.mark.parametrize("ttl", [0, -1, -300, 301, 600, 10**6, 10**12])
def test_out_of_range_ttl_rejected(
    backend: MockBackend, project_id: str, _auth: None, ttl: int
) -> None:
    tool = make_mint_magic_link_tool(backend)
    with pytest.raises(ValueError, match="ttl_seconds"):
        tool(project_id, ttl_seconds=ttl)


@pytest.mark.parametrize("ttl", [1, 60, 299, 300])
def test_in_range_ttl_accepted(
    backend: MockBackend, project_id: str, _auth: None, ttl: int
) -> None:
    tool = make_mint_magic_link_tool(backend)
    result = tool(project_id, ttl_seconds=ttl)
    assert result.url
    assert result.expires_at is not None
