"""Audit finding #9 — URL safety on the project-handoff surface.

``MockBackend.get_video_download_url`` previously interpolated the OAuth
``sub`` (a PII identifier) directly into a URL path, and the project_name
field was raw-interpolated everywhere with no validation pattern. The
Phase-3 adapter inherits this shape, so both fixes need to land before
real backend wiring is built.

What this test pins:

- ``ProjectInfo.project_name`` / ``ProjectSummary.project_name`` /
  ``ProjectDetail.project_name`` must satisfy ``^[A-Za-z0-9_-]{1,64}$``
  at model-construction time — path traversal (``../``), URL syntax
  characters (``?``, ``@``, ``/``, ``%``), and oversize names all fail
  to construct.
- ``mint_magic_link`` and ``get_video_download_url`` URLs must not
  contain the OAuth ``sub`` in any segment, and must carry an opaque
  server-generated token (not a guessable structure-based id).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from vividscripts_mcp.adapters.mock import MockBackend
from vividscripts_mcp.models import (
    ProjectDetail,
    ProjectInfo,
    ProjectSettings,
    ProjectSummary,
)
from vividscripts_mcp.oauth.bearer import UserClaims
from vividscripts_mcp.oauth.context import set_user_claims
from vividscripts_mcp.tools.handoff import (
    make_get_video_download_url_tool,
    make_mint_magic_link_tool,
)

# ---------------------------------------------------------------------------
# Pattern enforcement on the project_name model field.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_name",
    [
        "../escape",
        "..\\windows",
        "with/slash",
        "with?query",
        "with@at",
        "with%2Fencoded",
        "with space",
        "with.dot",
        "with#frag",
        "name!bang",
        "",
        "x" * 65,
    ],
)
def test_project_info_rejects_unsafe_name(bad_name: str) -> None:
    with pytest.raises(ValidationError):
        ProjectInfo(
            project_id="mock-0001",
            project_name=bad_name,
            editor_url="https://example.com/x",
            created_at=datetime.now(UTC),
        )


@pytest.mark.parametrize("bad_name", ["../escape", "name?x", "name@x", "with/slash", "x" * 65])
def test_project_summary_rejects_unsafe_name(bad_name: str) -> None:
    with pytest.raises(ValidationError):
        ProjectSummary(
            project_id="mock-0001",
            project_name=bad_name,
            status="draft",
            scene_count=0,
            created_at=datetime.now(UTC),
            editor_url="https://example.com/x",
        )


@pytest.mark.parametrize("bad_name", ["../escape", "name?x", "name@x"])
def test_project_detail_rejects_unsafe_name(bad_name: str) -> None:
    with pytest.raises(ValidationError):
        ProjectDetail(
            project_id="mock-0001",
            project_name=bad_name,
            metadata={},
            scene_summaries=[],
            video_status="none",
            editor_url="https://example.com/x",
        )


@pytest.mark.parametrize(
    "good_name",
    [
        "Untitled_Project_1",
        "My-Project",
        "abc123",
        "a",
        "_underscore",
        "-leading-dash",
        "X" * 64,
    ],
)
def test_project_info_accepts_safe_name(good_name: str) -> None:
    info = ProjectInfo(
        project_id="mock-0001",
        project_name=good_name,
        editor_url="https://example.com/x",
        created_at=datetime.now(UTC),
    )
    assert info.project_name == good_name


# ---------------------------------------------------------------------------
# URL shape — no PII (``sub``) in path or query.
# ---------------------------------------------------------------------------


@pytest.fixture
def backend() -> MockBackend:
    return MockBackend(base_url="https://app.vividscripts.test")


@pytest.fixture
def project_id(backend: MockBackend) -> str:
    return backend.create_project(
        user_id="esteban-castorena-sub-pii",
        story="A short story.",
        settings=ProjectSettings(),
    ).project_id


@pytest.fixture
def _auth() -> Iterator[None]:
    set_user_claims(
        UserClaims(
            sub="esteban-castorena-sub-pii",
            client_id="c",
            scope=None,
            jti="j-urlsafe",
            exp=9999999999,
            iat=1,
        )
    )
    yield
    set_user_claims(None)


def test_mint_magic_link_url_omits_sub_pii(
    backend: MockBackend, project_id: str, _auth: None
) -> None:
    out = make_mint_magic_link_tool(backend)(project_id)
    assert "esteban-castorena-sub-pii" not in out.url


def test_get_video_download_url_omits_sub_pii(
    backend: MockBackend, project_id: str, _auth: None
) -> None:
    out = make_get_video_download_url_tool(backend)(project_id)
    assert "esteban-castorena-sub-pii" not in out.url


def test_get_video_download_url_uses_opaque_token(
    backend: MockBackend, project_id: str, _auth: None
) -> None:
    """The URL must carry an opaque server-generated token segment, not
    a guessable ``{user_id}/{project_name}/output.mp4`` path."""
    out_a = make_get_video_download_url_tool(backend)(project_id)
    out_b = make_get_video_download_url_tool(backend)(project_id)
    # Two calls produce two different opaque tokens — otherwise the link
    # is effectively the same long-lived URL every time, defeating the
    # ``expires_at`` contract.
    assert out_a.url != out_b.url
