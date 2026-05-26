"""Tests for the project-management MCP tools (KAN-53)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from vividscripts_mcp.adapters.mock import MockBackend
from vividscripts_mcp.models import ProjectSettings
from vividscripts_mcp.oauth.bearer import UserClaims
from vividscripts_mcp.oauth.context import (
    AuthRequired,
    get_user_claims,
    set_user_claims,
)
from vividscripts_mcp.tools.projects import (
    make_create_project_tool,
    make_get_project_tool,
    make_list_projects_tool,
)


def _claims(user_id: str) -> UserClaims:
    return UserClaims(
        sub=user_id,
        client_id="test-client",
        scope=None,
        jti="test-jti",
        exp=2_000_000_000,
        iat=1_700_000_000,
    )


@pytest.fixture
def backend() -> MockBackend:
    return MockBackend(base_url="https://app.vividscripts.test")


@pytest.fixture
def alpha_session() -> Iterator[None]:
    """Bind UserClaims for user-alpha for the duration of the test."""
    set_user_claims(_claims("user-alpha"))
    yield
    set_user_claims(None)


@pytest.fixture
def beta_session() -> Iterator[None]:
    set_user_claims(_claims("user-beta"))
    yield
    set_user_claims(None)


@pytest.fixture
def settings() -> ProjectSettings:
    return ProjectSettings(style="dark_cinematic", voice="female", dimension="landscape")


# ---------------------------------------------------------------------------
# create_project
# ---------------------------------------------------------------------------


def test_create_project_returns_info_for_authenticated_user(
    backend: MockBackend,
    alpha_session: None,
    settings: ProjectSettings,
) -> None:
    tool = make_create_project_tool(backend)
    info = tool("I lived alone for years.", settings)
    assert info.project_id
    assert info.project_name
    assert info.editor_url.startswith("https://app.vividscripts.test")


def test_create_project_without_auth_raises(
    backend: MockBackend,
    settings: ProjectSettings,
) -> None:
    """No bound claims → AuthRequired raised."""
    set_user_claims(None)  # explicit
    tool = make_create_project_tool(backend)
    with pytest.raises(AuthRequired):
        tool("hello", settings)


def test_create_project_scopes_to_authenticated_user(
    backend: MockBackend,
    alpha_session: None,
    settings: ProjectSettings,
) -> None:
    """The project is created against the authenticated user_id, not a client-supplied one."""
    tool = make_create_project_tool(backend)
    info = tool("a story", settings)
    # Confirm the project shows up when listing under the same user_id —
    # not some other user's list.
    assert backend.get_project(user_id="user-alpha", project_id=info.project_id) is not None


# ---------------------------------------------------------------------------
# list_projects
# ---------------------------------------------------------------------------


def test_list_projects_returns_only_own_projects(
    backend: MockBackend,
    settings: ProjectSettings,
) -> None:
    """User A and User B see disjoint project lists."""
    create = make_create_project_tool(backend)
    listing = make_list_projects_tool(backend)

    set_user_claims(_claims("user-alpha"))
    alpha_info = create("alpha's story", settings)

    set_user_claims(_claims("user-beta"))
    beta_info = create("beta's story", settings)
    beta_listing = listing()

    set_user_claims(_claims("user-alpha"))
    alpha_listing = listing()

    alpha_ids = {p.project_id for p in alpha_listing}
    beta_ids = {p.project_id for p in beta_listing}
    assert alpha_info.project_id in alpha_ids
    assert alpha_info.project_id not in beta_ids
    assert beta_info.project_id in beta_ids
    assert beta_info.project_id not in alpha_ids


def test_list_projects_without_auth_raises(backend: MockBackend) -> None:
    set_user_claims(None)
    tool = make_list_projects_tool(backend)
    with pytest.raises(AuthRequired):
        tool()


def test_list_projects_empty_for_new_user(
    backend: MockBackend,
    alpha_session: None,
) -> None:
    tool = make_list_projects_tool(backend)
    assert tool() == []


# ---------------------------------------------------------------------------
# get_project
# ---------------------------------------------------------------------------


def test_get_project_returns_detail_for_owner(
    backend: MockBackend,
    alpha_session: None,
    settings: ProjectSettings,
) -> None:
    info = make_create_project_tool(backend)("my story", settings)
    detail = make_get_project_tool(backend)(info.project_id)
    assert detail.project_id == info.project_id
    assert detail.editor_url == info.editor_url


def test_get_project_cross_user_isolation(
    backend: MockBackend,
    settings: ProjectSettings,
) -> None:
    """User B can't fetch user A's project — backend raises KeyError."""
    create = make_create_project_tool(backend)
    get = make_get_project_tool(backend)

    set_user_claims(_claims("user-alpha"))
    info = create("alpha's story", settings)

    set_user_claims(_claims("user-beta"))
    with pytest.raises(KeyError) as exc:
        get(info.project_id)
    assert info.project_id in str(exc.value)


def test_get_project_without_auth_raises(backend: MockBackend) -> None:
    set_user_claims(None)
    with pytest.raises(AuthRequired):
        make_get_project_tool(backend)("any-id")


def test_get_project_unknown_id_raises_keyerror(
    backend: MockBackend,
    alpha_session: None,
) -> None:
    with pytest.raises(KeyError):
        make_get_project_tool(backend)("nonexistent-project")


# ---------------------------------------------------------------------------
# Context plumbing
# ---------------------------------------------------------------------------


def test_set_then_get_round_trip() -> None:
    claims = _claims("user-x")
    set_user_claims(claims)
    assert get_user_claims() == claims
    set_user_claims(None)
    assert get_user_claims() is None


def test_tools_registered_on_mcp_server() -> None:
    """The three project tools surface in FastMCP's tool catalog."""
    from vividscripts_mcp.server import create_mcp_server

    mcp = create_mcp_server(MockBackend())
    import asyncio

    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    assert {"create_project", "list_projects", "get_project"} <= names


# ---------------------------------------------------------------------------
# KAN-136 — per-asset render status on get_project
# ---------------------------------------------------------------------------


def test_get_project_new_project_reports_minimal_completeness(
    backend: MockBackend,
    alpha_session: None,
    settings: ProjectSettings,
) -> None:
    """A fresh project has nothing rendered → all asset flags False, rollup 'minimal'."""
    info = make_create_project_tool(backend)("my story", settings)
    detail = make_get_project_tool(backend)(info.project_id)
    assert detail.assets.music is False
    assert detail.assets.sfx is False
    assert detail.assets.thumbnail is False
    assert detail.assets.title_card is False
    assert detail.video_completeness == "minimal"


def test_get_project_asset_flags_flip_after_render_jobs(
    backend: MockBackend,
    alpha_session: None,
    settings: ProjectSettings,
) -> None:
    """Each generate_* job that completes updates the corresponding asset flag."""
    info = make_create_project_tool(backend)("my story", settings)
    user_id = "user-alpha"

    backend.submit_job(user_id, info.project_id, "generate_music", {})
    detail = make_get_project_tool(backend)(info.project_id)
    assert detail.assets.music is True
    assert detail.video_completeness == "partial"

    backend.submit_job(user_id, info.project_id, "generate_sfx", {})
    backend.submit_job(user_id, info.project_id, "generate_thumbnail", {})
    detail = make_get_project_tool(backend)(info.project_id)
    assert detail.assets.sfx is True
    assert detail.assets.thumbnail is True
    # title_card stays False — no render path yet (KAN-131).
    assert detail.assets.title_card is False
    # 'complete' rollup excludes title_card until KAN-131 wires it.
    assert detail.video_completeness == "complete"


def test_get_project_scene_summaries_carry_has_sfx(
    backend: MockBackend,
    alpha_session: None,
    settings: ProjectSettings,
) -> None:
    """generate_sfx marks every existing scene as having SFX."""
    info = make_create_project_tool(backend)("my story", settings)
    user_id = "user-alpha"
    backend.add_scene(user_id, info.project_id, after_index=-1, text="scene 1 text")
    backend.add_scene(user_id, info.project_id, after_index=0, text="scene 2 text")

    detail_before = make_get_project_tool(backend)(info.project_id)
    assert all(s["has_sfx"] is False for s in detail_before.scene_summaries)

    backend.submit_job(user_id, info.project_id, "generate_sfx", {})
    detail_after = make_get_project_tool(backend)(info.project_id)
    assert all(s["has_sfx"] is True for s in detail_after.scene_summaries)


def test_get_project_compile_video_sets_video_status_ready(
    backend: MockBackend,
    alpha_session: None,
    settings: ProjectSettings,
) -> None:
    """compile_video flips video_status to 'ready' (independent of asset flags)."""
    info = make_create_project_tool(backend)("my story", settings)
    user_id = "user-alpha"
    assert make_get_project_tool(backend)(info.project_id).video_status == "none"
    backend.submit_job(user_id, info.project_id, "compile_video", {})
    assert make_get_project_tool(backend)(info.project_id).video_status == "ready"


def test_duplicate_project_inherits_asset_state(
    backend: MockBackend,
    alpha_session: None,
    settings: ProjectSettings,
) -> None:
    """A duplicate carries forward the original's render state."""
    info = make_create_project_tool(backend)("my story", settings)
    user_id = "user-alpha"
    backend.submit_job(user_id, info.project_id, "generate_music", {})
    backend.submit_job(user_id, info.project_id, "generate_thumbnail", {})

    dup_info = backend.duplicate_project(user_id, info.project_id, new_name="copy")
    dup_detail = backend.get_project(user_id, dup_info.project_id)
    assert dup_detail.assets.music is True
    assert dup_detail.assets.thumbnail is True
    assert dup_detail.assets.sfx is False
    assert dup_detail.video_completeness == "partial"
