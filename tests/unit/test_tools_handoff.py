"""KAN-77 — URL-handoff tools (mint_magic_link, get_video_download_url)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from vividscripts_mcp.adapters.mock import MockBackend
from vividscripts_mcp.models import MagicLinkUrl, ProjectSettings
from vividscripts_mcp.oauth.bearer import UserClaims
from vividscripts_mcp.oauth.context import AuthRequired, set_user_claims
from vividscripts_mcp.tools.handoff import (
    make_get_video_download_url_tool,
    make_mint_magic_link_tool,
)


@pytest.fixture
def backend() -> MockBackend:
    return MockBackend(base_url="https://app.vividscripts.test")


@pytest.fixture
def project_id(backend: MockBackend) -> str:
    return backend.create_project(
        user_id="user-alpha", story="A story.", settings=ProjectSettings()
    ).project_id


@pytest.fixture
def _auth() -> Iterator[None]:
    set_user_claims(
        UserClaims(sub="user-alpha", client_id="c", scope=None, jti="j", exp=9999999999, iat=1)
    )
    yield
    set_user_claims(None)


def test_mint_magic_link_returns_typed_url(
    backend: MockBackend, project_id: str, _auth: None
) -> None:
    out = make_mint_magic_link_tool(backend)(project_id)
    assert isinstance(out, MagicLinkUrl)
    assert "/m/" in out.url
    assert "view=editor" in out.url
    assert out.expires_at is not None


def test_mint_magic_link_view_passthrough(
    backend: MockBackend, project_id: str, _auth: None
) -> None:
    out = make_mint_magic_link_tool(backend)(project_id, view="video")
    assert "view=video" in out.url


def test_get_video_download_url_typed(backend: MockBackend, project_id: str, _auth: None) -> None:
    out = make_get_video_download_url_tool(backend)(project_id)
    assert isinstance(out, MagicLinkUrl)
    assert out.url.startswith("https://app.vividscripts.test/")
    assert out.expires_at is not None


def test_handoff_tools_require_auth(backend: MockBackend, project_id: str) -> None:
    with pytest.raises(AuthRequired):
        make_mint_magic_link_tool(backend)(project_id)
    with pytest.raises(AuthRequired):
        make_get_video_download_url_tool(backend)(project_id)


def test_get_video_download_url_cross_user_isolated(
    backend: MockBackend, project_id: str, _auth: None
) -> None:
    # A different user's claims must not reach user-alpha's project.
    set_user_claims(
        UserClaims(sub="user-beta", client_id="c", scope=None, jti="j2", exp=9999999999, iat=1)
    )
    try:
        with pytest.raises(KeyError):
            make_get_video_download_url_tool(backend)(project_id)
    finally:
        set_user_claims(None)
