"""Tests for the workflow-state + custom-override tools (KAN-59)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from vividscripts_mcp.adapters.mock import MockBackend
from vividscripts_mcp.models import ProjectSettings, StepResultOutcome, WorkflowState
from vividscripts_mcp.oauth.bearer import UserClaims
from vividscripts_mcp.oauth.context import AuthRequired, set_user_claims
from vividscripts_mcp.tools.state import (
    CustomOverride,
    OverrideAck,
    make_get_custom_prompt_override_tool,
    make_get_workflow_state_tool,
    make_save_step_result_tool,
    make_set_custom_prompt_override_tool,
)

_FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "step_results"


def _claims(sub: str = "user-alpha") -> UserClaims:
    return UserClaims(
        sub=sub,
        client_id="c",
        scope=None,
        jti="j",
        exp=2_000_000_000,
        iat=1_700_000_000,
    )


@pytest.fixture
def backend() -> MockBackend:
    return MockBackend(base_url="https://app.vividscripts.test")


@pytest.fixture
def project_id(backend: MockBackend) -> str:
    info = backend.create_project(
        user_id="user-alpha",
        story="A man, alone. Or so he thought.",
        settings=ProjectSettings(style="dark_cinematic", voice="female", dimension="landscape"),
    )
    return info.project_id


@pytest.fixture
def authed() -> Iterator[None]:
    set_user_claims(_claims())
    yield
    set_user_claims(None)


def _valid(step: str) -> dict[str, object]:
    return json.loads((_FIXTURE_DIR / f"{step}.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# save_step_result
# ---------------------------------------------------------------------------


def test_save_valid_result_persists_and_returns_next_step(
    backend: MockBackend, project_id: str, authed: None
) -> None:
    tool = make_save_step_result_tool(backend)
    outcome = tool(project_id, "story_blueprint", _valid("story_blueprint"))
    assert isinstance(outcome, StepResultOutcome)
    assert outcome.success is True
    assert outcome.validation_errors is None
    assert outcome.next_step is not None
    # Persisted in the backend's workflow state
    state = backend.get_workflow_state(user_id="user-alpha", project_id=project_id)
    assert "story_blueprint" in state.completed_steps
    assert state.current_data["story_blueprint"] == _valid("story_blueprint")


def test_save_invalid_result_rejected_and_persists_nothing(
    backend: MockBackend, project_id: str, authed: None
) -> None:
    tool = make_save_step_result_tool(backend)
    # story_blueprint requires genre/tone/etc.; send junk
    outcome = tool(project_id, "story_blueprint", {"genre": "horror"})
    assert outcome.success is False
    assert outcome.validation_errors
    assert outcome.next_step is None
    # Nothing persisted
    state = backend.get_workflow_state(user_id="user-alpha", project_id=project_id)
    assert "story_blueprint" not in state.completed_steps


def test_save_unknown_step_is_validation_failure(
    backend: MockBackend, project_id: str, authed: None
) -> None:
    tool = make_save_step_result_tool(backend)
    outcome = tool(project_id, "not_a_real_step", {"x": 1})
    assert outcome.success is False
    assert outcome.validation_errors == ["unknown step: 'not_a_real_step'"]


def test_save_validation_errors_have_field_paths(
    backend: MockBackend, project_id: str, authed: None
) -> None:
    tool = make_save_step_result_tool(backend)
    bad = {
        "genre": "horror",
        "tone": "dread",
        "narrative_structure": "three-act",
        "creative_direction": "x",
        "paragraph_analyses": [{"paragraph_index": 0, "tension": "high"}],  # wrong type
    }
    outcome = tool(project_id, "story_blueprint", bad)
    assert outcome.success is False
    assert any("paragraph_analyses.0.tension" in e for e in outcome.validation_errors or [])


def test_save_requires_auth(backend: MockBackend, project_id: str) -> None:
    set_user_claims(None)
    tool = make_save_step_result_tool(backend)
    with pytest.raises(AuthRequired):
        tool(project_id, "story_blueprint", _valid("story_blueprint"))


def test_save_is_user_scoped(backend: MockBackend, project_id: str) -> None:
    """A different user can't save into user-alpha's project."""
    set_user_claims(_claims("user-beta"))
    tool = make_save_step_result_tool(backend)
    with pytest.raises(KeyError):
        tool(project_id, "story_blueprint", _valid("story_blueprint"))
    set_user_claims(None)


# ---------------------------------------------------------------------------
# get_workflow_state
# ---------------------------------------------------------------------------


def test_get_workflow_state_returns_state(
    backend: MockBackend, project_id: str, authed: None
) -> None:
    tool = make_get_workflow_state_tool(backend)
    state = tool(project_id)
    assert isinstance(state, WorkflowState)
    assert state.project_id == project_id
    assert state.status == "not_started"
    assert state.completed_steps == []


def test_get_workflow_state_reflects_saved_steps(
    backend: MockBackend, project_id: str, authed: None
) -> None:
    make_save_step_result_tool(backend)(project_id, "story_blueprint", _valid("story_blueprint"))
    state = make_get_workflow_state_tool(backend)(project_id)
    assert "story_blueprint" in state.completed_steps
    assert state.status == "in_progress"


def test_get_workflow_state_requires_auth(backend: MockBackend, project_id: str) -> None:
    set_user_claims(None)
    with pytest.raises(AuthRequired):
        make_get_workflow_state_tool(backend)(project_id)


# ---------------------------------------------------------------------------
# custom prompt overrides
# ---------------------------------------------------------------------------


def test_override_round_trip(backend: MockBackend, authed: None) -> None:
    setter = make_set_custom_prompt_override_tool(backend)
    getter = make_get_custom_prompt_override_tool(backend)

    before = getter("title_generator")
    assert isinstance(before, CustomOverride)
    assert before.has_override is False
    assert before.template is None

    ack = setter("title_generator", "MY CUSTOM TITLE PROMPT")
    assert isinstance(ack, OverrideAck)
    assert ack.success is True

    after = getter("title_generator")
    assert after.has_override is True
    assert after.template == "MY CUSTOM TITLE PROMPT"


def test_set_override_rejects_unknown_prompt(backend: MockBackend, authed: None) -> None:
    setter = make_set_custom_prompt_override_tool(backend)
    with pytest.raises(ValueError, match="unknown prompt"):
        setter("not_a_prompt", "whatever")


def test_override_is_user_scoped(backend: MockBackend) -> None:
    """user-beta's override doesn't leak into user-alpha's get."""
    set_user_claims(_claims("user-beta"))
    make_set_custom_prompt_override_tool(backend)("thumbnail", "BETA OVERRIDE")

    set_user_claims(_claims("user-alpha"))
    result = make_get_custom_prompt_override_tool(backend)("thumbnail")
    assert result.has_override is False
    set_user_claims(None)


def test_override_tools_require_auth(backend: MockBackend) -> None:
    set_user_claims(None)
    with pytest.raises(AuthRequired):
        make_get_custom_prompt_override_tool(backend)("thumbnail")
    with pytest.raises(AuthRequired):
        make_set_custom_prompt_override_tool(backend)("thumbnail", "x")


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------


async def test_all_state_tools_registered(backend: MockBackend) -> None:
    from vividscripts_mcp.server import create_mcp_server

    mcp = create_mcp_server(backend)
    names = {t.name for t in await mcp.list_tools()}
    assert {
        "save_step_result",
        "get_workflow_state",
        "get_custom_prompt_override",
        "set_custom_prompt_override",
    } <= names
