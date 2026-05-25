"""KAN-69 — media tools (generate_audio + check_job)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from vividscripts_mcp.adapters.mock import MockBackend
from vividscripts_mcp.models import JobStatus, MusicSelection, ProjectSettings
from vividscripts_mcp.oauth.bearer import UserClaims
from vividscripts_mcp.oauth.context import AuthRequired, set_user_claims
from vividscripts_mcp.tools.media import (
    JobSubmission,
    make_check_job_tool,
    make_compile_video_tool,
    make_generate_audio_tool,
    make_generate_images_tool,
    make_generate_music_tool,
    make_generate_sfx_tool,
    make_generate_thumbnail_tool,
    make_regenerate_scene_audio_tool,
    make_regenerate_scene_image_tool,
    make_select_music_tool,
)

# animate_scene factory dropped 2026-05-25 (Test 2 post-mortem): the MCP tool
# no longer exposes the Kling animation entrypoint to keep cost off the
# default routine. The backend still supports the job; the web UI still calls
# it. See tools/media.py for the matching catalog change.
_GENERATE_FACTORIES = [
    (make_generate_audio_tool, "generate_audio"),
    (make_generate_images_tool, "generate_images"),
    (make_generate_sfx_tool, "generate_sfx"),
    (make_generate_thumbnail_tool, "generate_thumbnail"),
    (make_generate_music_tool, "generate_music"),
    (make_compile_video_tool, "compile_video"),
]


def _claims(sub: str = "user-alpha") -> UserClaims:
    return UserClaims(
        sub=sub,
        client_id="c",
        scope=None,
        jti="j",
        exp=9999999999,
        iat=1,
    )


@pytest.fixture
def backend() -> MockBackend:
    return MockBackend(base_url="https://app.vividscripts.test")


@pytest.fixture
def project_id(backend: MockBackend) -> str:
    info = backend.create_project(
        user_id="user-alpha",
        story="A story.",
        settings=ProjectSettings(),
    )
    return info.project_id


@pytest.fixture
def _auth() -> Iterator[None]:
    set_user_claims(_claims())
    yield
    set_user_claims(None)


def test_generate_audio_returns_job_handle(
    backend: MockBackend, project_id: str, _auth: None
) -> None:
    tool = make_generate_audio_tool(backend)
    sub = tool(project_id)
    assert isinstance(sub, JobSubmission)
    assert sub.job_id
    assert sub.job_type == "generate_audio"


def test_check_job_round_trips(backend: MockBackend, project_id: str, _auth: None) -> None:
    job_id = make_generate_audio_tool(backend)(project_id).job_id
    status = make_check_job_tool(backend)(job_id)
    assert isinstance(status, JobStatus)
    assert status.job_id == job_id
    assert status.job_type == "generate_audio"
    assert status.status in {"queued", "running", "completed", "failed"}


def test_check_job_unknown_id_raises(backend: MockBackend, _auth: None) -> None:
    with pytest.raises(KeyError):
        make_check_job_tool(backend)("no-such-job")


def test_generate_audio_requires_auth(backend: MockBackend, project_id: str) -> None:
    # No set_user_claims → require_user_claims must reject.
    with pytest.raises(AuthRequired):
        make_generate_audio_tool(backend)(project_id)


def test_check_job_requires_auth(backend: MockBackend) -> None:
    with pytest.raises(AuthRequired):
        make_check_job_tool(backend)("any")


@pytest.mark.parametrize(("factory", "job_type"), _GENERATE_FACTORIES)
def test_all_generate_tools_return_typed_job_handle(
    backend: MockBackend, project_id: str, _auth: None, factory, job_type
) -> None:
    sub = factory(backend)(project_id)
    assert isinstance(sub, JobSubmission)
    assert sub.job_id
    assert sub.job_type == job_type
    # And the job is then pollable by the same backend.
    assert make_check_job_tool(backend)(sub.job_id).job_type == job_type


@pytest.mark.parametrize(("factory", "job_type"), _GENERATE_FACTORIES)
def test_all_generate_tools_require_auth(
    backend: MockBackend, project_id: str, factory, job_type
) -> None:
    with pytest.raises(AuthRequired):
        factory(backend)(project_id)


def test_select_music_is_synchronous_and_records_mood(
    backend: MockBackend, project_id: str, _auth: None
) -> None:
    sel = make_select_music_tool(backend)(project_id, "dark-tension")
    assert isinstance(sel, MusicSelection)
    assert sel.mood == "dark-tension"
    assert sel.available_tracks  # the mock catalog has dark-tension tracks
    assert sel.needs_generation is False
    # Mood is now recorded on the project.
    detail = backend.get_project("user-alpha", project_id)
    assert detail.metadata["settings"]["music_mood"] == "dark-tension"


def test_select_music_unknown_mood_needs_generation(
    backend: MockBackend, project_id: str, _auth: None
) -> None:
    sel = make_select_music_tool(backend)(project_id, "whimsical-jazz")
    assert sel.available_tracks == []
    assert sel.needs_generation is True


def test_select_music_requires_auth(backend: MockBackend, project_id: str) -> None:
    with pytest.raises(AuthRequired):
        make_select_music_tool(backend)(project_id, "dark-tension")


@pytest.mark.parametrize(
    ("factory", "job_type"),
    [
        (make_regenerate_scene_image_tool, "regenerate_scene_image"),
        (make_regenerate_scene_audio_tool, "regenerate_scene_audio"),
    ],
)
def test_regenerate_scene_tools_return_job_handle(
    backend: MockBackend, project_id: str, _auth: None, factory, job_type
) -> None:
    sub = factory(backend)(project_id, 0)
    assert isinstance(sub, JobSubmission)
    assert sub.job_id
    assert sub.job_type == job_type
    assert make_check_job_tool(backend)(sub.job_id).job_type == job_type


@pytest.mark.parametrize(
    "factory",
    [make_regenerate_scene_image_tool, make_regenerate_scene_audio_tool],
)
def test_regenerate_scene_tools_require_auth(
    backend: MockBackend, project_id: str, factory
) -> None:
    with pytest.raises(AuthRequired):
        factory(backend)(project_id, 0)
