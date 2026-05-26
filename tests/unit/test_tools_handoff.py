"""KAN-77 — URL-handoff tools (mint_magic_link, get_video_download_url).

KAN-132 adds ``get_thumbnail_download_url``; the same patterns apply.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from vividscripts_mcp.adapters.mock import MockBackend
from vividscripts_mcp.models import MagicLinkUrl, ProjectSettings
from vividscripts_mcp.oauth.bearer import UserClaims
from vividscripts_mcp.oauth.context import AuthRequired, set_user_claims
from vividscripts_mcp.tools.handoff import (
    make_get_thumbnail_download_url_tool,
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


# ---------------------------------------------------------------------------
# KAN-132 — get_thumbnail_download_url
# ---------------------------------------------------------------------------


def test_get_thumbnail_download_url_typed(
    backend: MockBackend, project_id: str, _auth: None
) -> None:
    """After generate_thumbnail completes, the tool returns a typed URL."""
    backend.submit_job("user-alpha", project_id, "generate_thumbnail", {})
    out = make_get_thumbnail_download_url_tool(backend)(project_id)
    assert isinstance(out, MagicLinkUrl)
    assert out.url.startswith("https://app.vividscripts.test/")
    assert "/t/" in out.url
    assert out.url.endswith("thumbnail.png")
    assert out.expires_at is not None


def test_get_thumbnail_download_url_refuses_before_render(
    backend: MockBackend, project_id: str, _auth: None
) -> None:
    """Calling before generate_thumbnail has run raises LookupError.

    This is the core KAN-132 acceptance criterion: no silent "okay,
    here's a URL" response when nothing has been rendered yet.
    """
    with pytest.raises(LookupError) as exc:
        make_get_thumbnail_download_url_tool(backend)(project_id)
    assert "generate_thumbnail" in str(exc.value)


def test_get_thumbnail_download_url_requires_auth(backend: MockBackend, project_id: str) -> None:
    with pytest.raises(AuthRequired):
        make_get_thumbnail_download_url_tool(backend)(project_id)


def test_get_thumbnail_download_url_cross_user_isolated(
    backend: MockBackend, project_id: str, _auth: None
) -> None:
    """A different user's claims must not reach user-alpha's project."""
    backend.submit_job("user-alpha", project_id, "generate_thumbnail", {})
    set_user_claims(
        UserClaims(sub="user-beta", client_id="c", scope=None, jti="j2", exp=9999999999, iat=1)
    )
    try:
        with pytest.raises(KeyError):
            make_get_thumbnail_download_url_tool(backend)(project_id)
    finally:
        set_user_claims(None)


def test_get_thumbnail_download_url_token_rotates(
    backend: MockBackend, project_id: str, _auth: None
) -> None:
    """Two consecutive calls return different tokens (same opaque-token
    discipline as get_video_download_url)."""
    backend.submit_job("user-alpha", project_id, "generate_thumbnail", {})
    tool = make_get_thumbnail_download_url_tool(backend)
    first = tool(project_id).url
    second = tool(project_id).url
    assert first != second


def test_handoff_tools_registered_on_mcp_server() -> None:
    """The new tool shows up in FastMCP's tool catalog alongside the existing two."""
    from vividscripts_mcp.server import create_mcp_server

    mcp = create_mcp_server(MockBackend())
    import asyncio

    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    assert {
        "mint_magic_link",
        "get_video_download_url",
        "get_thumbnail_download_url",
    } <= names
