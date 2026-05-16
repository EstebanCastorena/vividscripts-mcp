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
    make_generate_audio_tool,
)


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
