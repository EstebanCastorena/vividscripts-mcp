"""KAN-69 — media tools (generate_audio + check_job)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from vividscripts_mcp.adapters.mock import MockBackend
from vividscripts_mcp.models import JobStatus, ProjectSettings
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
)

# animate_scene factory dropped 2026-05-25 (Test 2 post-mortem): the MCP tool
# no longer exposes the Kling animation entrypoint to keep cost off the
# default routine. The backend still supports the job; the web UI still calls
# it. See tools/media.py for the matching catalog change.
#
# compile_video factory pulled out (KAN-130): it now enforces preconditions
# and has a different call shape (skip_* kwargs). Tested separately below.
#
# generate_music pulled out (KAN-126): it now takes a required ``mood`` arg
# (the former select_music probe was folded in), so it can't ride the generic
# single-arg parametrized cases. Tested separately below.
_GENERATE_FACTORIES = [
    (make_generate_audio_tool, "generate_audio"),
    (make_generate_images_tool, "generate_images"),
    (make_generate_sfx_tool, "generate_sfx"),
    (make_generate_thumbnail_tool, "generate_thumbnail"),
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


# ---------------------------------------------------------------------------
# KAN-126 — generate_music: mood is a required arg; the former synchronous
# select_music probe is folded in (records mood + submits the job in one call).
# ---------------------------------------------------------------------------


def test_generate_music_returns_job_handle_and_records_mood(
    backend: MockBackend, project_id: str, _auth: None
) -> None:
    sub = make_generate_music_tool(backend)(project_id, "dark-tension")
    assert isinstance(sub, JobSubmission)
    assert sub.job_id
    assert sub.job_type == "generate_music"
    # The job is pollable by the same backend.
    assert make_check_job_tool(backend)(sub.job_id).job_type == "generate_music"
    # And the mood was recorded on the project as part of the single call —
    # no separate select_music round-trip needed (KAN-126).
    detail = backend.get_project("user-alpha", project_id)
    assert detail.metadata["settings"]["music_mood"] == "dark-tension"


def test_generate_music_works_on_cold_project_in_one_call(
    backend: MockBackend, project_id: str, _auth: None
) -> None:
    """A brand-new project lands a music job with a single call, even for a
    mood the catalog has no pre-rendered tracks for (the cold-start case that
    used to cost a no-op select_music probe first — KAN-126)."""
    sub = make_generate_music_tool(backend)(project_id, "whimsical-jazz")
    assert sub.job_type == "generate_music"
    detail = backend.get_project("user-alpha", project_id)
    assert detail.metadata["settings"]["music_mood"] == "whimsical-jazz"


def test_generate_music_empty_mood_raises(
    backend: MockBackend, project_id: str, _auth: None
) -> None:
    with pytest.raises(ValueError, match="mood"):
        make_generate_music_tool(backend)(project_id, "")


def test_generate_music_requires_auth(backend: MockBackend, project_id: str) -> None:
    with pytest.raises(AuthRequired):
        make_generate_music_tool(backend)(project_id, "dark-tension")


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


# ---------------------------------------------------------------------------
# KAN-130 — compile_video preconditions
# ---------------------------------------------------------------------------


def _render_all_layers(backend: MockBackend, project_id: str) -> None:
    """Helper: drive every optional layer to rendered."""
    for job in ("generate_music", "generate_sfx", "generate_thumbnail"):
        backend.submit_job("user-alpha", project_id, job, {})


def test_compile_video_requires_auth(backend: MockBackend, project_id: str) -> None:
    """No bound claims → AuthRequired (fires before precondition check)."""
    with pytest.raises(AuthRequired):
        make_compile_video_tool(backend)(project_id)


def test_compile_video_refuses_when_layers_missing(
    backend: MockBackend, project_id: str, _auth: None
) -> None:
    """A fresh project has no optional layers → compile_video raises ValueError."""
    with pytest.raises(ValueError, match="preconditions not met") as exc:
        make_compile_video_tool(backend)(project_id)
    msg = str(exc.value)
    # Each missing asset names itself and its generate_* job.
    assert "music" in msg
    assert "sfx" in msg
    assert "thumbnail" in msg
    assert "generate_music" in msg
    assert "skip_music=True" in msg


def test_compile_video_refuses_when_partial_layers_missing(
    backend: MockBackend, project_id: str, _auth: None
) -> None:
    """If music + thumbnail are rendered but sfx isn't, only sfx is blocked."""
    backend.submit_job("user-alpha", project_id, "generate_music", {})
    backend.submit_job("user-alpha", project_id, "generate_thumbnail", {})
    with pytest.raises(ValueError, match="missing: sfx") as exc:
        make_compile_video_tool(backend)(project_id)
    msg = str(exc.value)
    assert "music" not in msg  # only the genuinely missing layer is named
    assert "generate_sfx" in msg


def test_compile_video_proceeds_when_all_layers_rendered(
    backend: MockBackend, project_id: str, _auth: None
) -> None:
    """All three layers rendered → compile submits a job and returns a handle."""
    _render_all_layers(backend, project_id)
    sub = make_compile_video_tool(backend)(project_id)
    assert isinstance(sub, JobSubmission)
    assert sub.job_type == "compile_video"
    # And the submitted job is pollable.
    assert make_check_job_tool(backend)(sub.job_id).job_type == "compile_video"


def test_compile_video_skip_flags_allow_proceeding(
    backend: MockBackend, project_id: str, _auth: None
) -> None:
    """All three skip flags → compile proceeds even on a bare project."""
    sub = make_compile_video_tool(backend)(
        project_id, skip_music=True, skip_sfx=True, skip_thumbnail=True
    )
    assert isinstance(sub, JobSubmission)
    assert sub.job_type == "compile_video"


def test_compile_video_partial_skip_only_covers_skipped_layers(
    backend: MockBackend, project_id: str, _auth: None
) -> None:
    """Skipping music alone, with sfx + thumbnail still missing, still refuses."""
    with pytest.raises(ValueError, match="missing: sfx, thumbnail"):
        make_compile_video_tool(backend)(project_id, skip_music=True)


def test_compile_video_refuses_while_render_jobs_running(
    backend: MockBackend, project_id: str, _auth: None
) -> None:
    """An in-flight render job blocks compile — this is the race condition
    that shipped a music-less video in the 2026-05-26 post-mortem."""
    # Simulate the music job still running. The mock's submit_job completes
    # synchronously, so we poke the internal state directly to exercise the
    # branch the real backend would naturally hit.
    state = backend._projects[("user-alpha", project_id)]
    state.running_job_types.add("generate_music")

    with pytest.raises(ValueError, match="still running") as exc:
        make_compile_video_tool(backend)(project_id)
    assert "music" in str(exc.value)


def test_compile_video_skips_are_forwarded_in_params(
    backend: MockBackend, project_id: str, _auth: None
) -> None:
    """When skips are used, the submitted job records which layers were
    intentionally omitted (useful for audit + future completeness rollup)."""
    sub = make_compile_video_tool(backend)(
        project_id, skip_music=True, skip_sfx=True, skip_thumbnail=True
    )
    job = backend._jobs[sub.job_id]
    assert job.result is not None
    assert job.result["params"]["skip"] == ["music", "sfx", "thumbnail"]


def test_check_compile_readiness_reflects_state(backend: MockBackend, project_id: str) -> None:
    """The backend-level readiness check mirrors compile_video's view."""
    readiness = backend.check_compile_readiness("user-alpha", project_id)
    assert readiness.ready is False
    assert set(readiness.missing) == {"music", "sfx", "thumbnail"}
    assert readiness.running == []

    backend.submit_job("user-alpha", project_id, "generate_music", {})
    readiness = backend.check_compile_readiness("user-alpha", project_id)
    assert readiness.ready is False
    assert set(readiness.missing) == {"sfx", "thumbnail"}

    backend.submit_job("user-alpha", project_id, "generate_sfx", {})
    backend.submit_job("user-alpha", project_id, "generate_thumbnail", {})
    readiness = backend.check_compile_readiness("user-alpha", project_id)
    assert readiness.ready is True
    assert readiness.missing == []
    assert readiness.running == []
