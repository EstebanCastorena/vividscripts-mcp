"""Unit tests for MockBackend.

Proves the BackendProtocol contract is implementable, that user-scoping
works, and that workflow state advances correctly.
"""

from __future__ import annotations

import pytest

from vividscripts_mcp.adapters import BackendProtocol, MockBackend
from vividscripts_mcp.adapters.mock import WORKFLOW_STEPS
from vividscripts_mcp.models import ProjectSettings


def test_mock_backend_implements_protocol(backend: MockBackend) -> None:
    """MockBackend must satisfy the BackendProtocol structural type."""
    assert isinstance(backend, BackendProtocol)


def test_create_and_list_projects(
    backend: MockBackend, user_id: str, settings: ProjectSettings, sample_story: str
) -> None:
    assert backend.list_projects(user_id) == []
    info = backend.create_project(user_id, sample_story, settings)
    assert info.project_id.startswith("mock-")
    assert info.editor_url.startswith("https://app.vividscripts.test/studio")
    listed = backend.list_projects(user_id)
    assert len(listed) == 1
    assert listed[0].project_id == info.project_id
    assert listed[0].status == "draft"
    assert listed[0].scene_count == 0


def test_get_project_returns_full_detail(
    backend: MockBackend, user_id: str, settings: ProjectSettings, sample_story: str
) -> None:
    info = backend.create_project(user_id, sample_story, settings)
    detail = backend.get_project(user_id, info.project_id)
    assert detail.project_id == info.project_id
    assert detail.video_status == "none"
    assert detail.metadata["settings"]["style"] == "dark_cinematic"


def test_users_cant_see_each_others_projects(
    backend: MockBackend,
    user_id: str,
    other_user_id: str,
    settings: ProjectSettings,
    sample_story: str,
) -> None:
    info = backend.create_project(user_id, sample_story, settings)
    assert backend.list_projects(other_user_id) == []
    with pytest.raises(KeyError):
        backend.get_project(other_user_id, info.project_id)


def test_delete_project(
    backend: MockBackend, user_id: str, settings: ProjectSettings, sample_story: str
) -> None:
    info = backend.create_project(user_id, sample_story, settings)
    backend.delete_project(user_id, info.project_id)
    assert backend.list_projects(user_id) == []


def test_duplicate_project_preserves_state(
    backend: MockBackend, user_id: str, settings: ProjectSettings, sample_story: str
) -> None:
    src = backend.create_project(user_id, sample_story, settings)
    backend.save_step_result(user_id, src.project_id, "project_setup", {})
    dup = backend.duplicate_project(user_id, src.project_id, new_name="MyDuplicate")
    assert dup.project_id != src.project_id
    assert dup.project_name == "MyDuplicate"
    state = backend.get_workflow_state(user_id, dup.project_id)
    assert "project_setup" in state.completed_steps


def test_workflow_advances(
    backend: MockBackend, user_id: str, settings: ProjectSettings, sample_story: str
) -> None:
    info = backend.create_project(user_id, sample_story, settings)
    initial = backend.get_workflow_state(user_id, info.project_id)
    assert initial.status == "not_started"
    assert initial.current_step == "project_setup"

    backend.save_step_result(user_id, info.project_id, "project_setup", {})
    after_first = backend.get_workflow_state(user_id, info.project_id)
    assert after_first.status == "in_progress"
    assert after_first.completed_steps == ["project_setup"]
    assert after_first.current_step == "story_blueprint"


def test_save_step_result_rejects_empty_step_name(
    backend: MockBackend, user_id: str, settings: ProjectSettings, sample_story: str
) -> None:
    """KAN-59 moved step-name validation to the save_step_result *tool*
    (schema-backed, prompt namespace). The mock no longer second-guesses
    the name against WORKFLOW_STEPS — it only rejects an empty name.

    (Was test_save_step_result_rejects_unknown_steps; an arbitrary
    unknown name is now accepted by the mock because the tool's
    validate_step_result is the authoritative gate.)
    """
    info = backend.create_project(user_id, sample_story, settings)
    outcome = backend.save_step_result(user_id, info.project_id, "   ", {})
    assert not outcome.success
    assert outcome.validation_errors is not None
    assert any("non-empty" in e for e in outcome.validation_errors)


def test_save_step_result_is_idempotent(
    backend: MockBackend, user_id: str, settings: ProjectSettings, sample_story: str
) -> None:
    info = backend.create_project(user_id, sample_story, settings)
    backend.save_step_result(user_id, info.project_id, "project_setup", {})
    backend.save_step_result(user_id, info.project_id, "project_setup", {})
    state = backend.get_workflow_state(user_id, info.project_id)
    assert state.completed_steps.count("project_setup") == 1


def test_workflow_completes_after_all_steps(
    backend: MockBackend, user_id: str, settings: ProjectSettings, sample_story: str
) -> None:
    info = backend.create_project(user_id, sample_story, settings)
    for step in WORKFLOW_STEPS:
        backend.save_step_result(user_id, info.project_id, step.name, {})
    state = backend.get_workflow_state(user_id, info.project_id)
    assert state.status == "completed"
    assert state.current_step is None


def test_list_workflow_steps_returns_pipeline(backend: MockBackend) -> None:
    steps = backend.list_workflow_steps()
    names = [s.name for s in steps]
    assert "story_blueprint" in names
    assert "video_compilation" in names
    assert names[0] == "project_setup"


def test_custom_prompt_overrides_round_trip(backend: MockBackend, user_id: str) -> None:
    assert backend.get_custom_prompt_override(user_id, "title_generator") is None
    backend.set_custom_prompt_override(user_id, "title_generator", "MY CUSTOM PROMPT")
    assert backend.get_custom_prompt_override(user_id, "title_generator") == "MY CUSTOM PROMPT"


def test_format_prompt_uses_custom_override_when_set(backend: MockBackend, user_id: str) -> None:
    backend.set_custom_prompt_override(user_id, "title_generator", "CUSTOM OVERRIDE")
    payload = backend.format_prompt(user_id, "title_generator", {"foo": "bar"})
    assert payload.prompt == "CUSTOM OVERRIDE"
    assert payload.step_name == "title_generator"


def test_submit_and_check_job(
    backend: MockBackend, user_id: str, settings: ProjectSettings, sample_story: str
) -> None:
    info = backend.create_project(user_id, sample_story, settings)
    job_id = backend.submit_job(
        user_id, info.project_id, "generate_audio", {"scenes": [{"text": "hi"}]}
    )
    status = backend.check_job(user_id, job_id)
    assert status.status == "completed"
    assert status.job_type == "generate_audio"
    assert status.progress == 1.0


def test_check_job_unknown_id_raises(backend: MockBackend, user_id: str) -> None:
    with pytest.raises(KeyError):
        backend.check_job(user_id, "does-not-exist")


def test_scene_crud(
    backend: MockBackend, user_id: str, settings: ProjectSettings, sample_story: str
) -> None:
    info = backend.create_project(user_id, sample_story, settings)
    assert backend.get_scenes(user_id, info.project_id) == []

    idx0 = backend.add_scene(user_id, info.project_id, after_index=-1, text="first")
    idx1 = backend.add_scene(user_id, info.project_id, after_index=idx0, text="second")
    assert idx0 == 0
    assert idx1 == 1

    scenes = backend.get_scenes(user_id, info.project_id)
    assert [s.text for s in scenes] == ["first", "second"]

    backend.update_scene(user_id, info.project_id, 0, {"text": "first updated"})
    assert backend.get_scene(user_id, info.project_id, 0).text == "first updated"

    backend.remove_scene(user_id, info.project_id, 0)
    remaining = backend.get_scenes(user_id, info.project_id)
    assert [s.text for s in remaining] == ["second"]


def test_mint_magic_link(
    backend: MockBackend, user_id: str, settings: ProjectSettings, sample_story: str
) -> None:
    info = backend.create_project(user_id, sample_story, settings)
    url, expires_at = backend.mint_magic_link(
        user_id, info.project_id, view="editor", ttl_seconds=60
    )
    assert url.startswith("https://app.vividscripts.test/m/")
    assert "view=editor" in url
    assert expires_at > info.created_at


def test_video_download_url(
    backend: MockBackend, user_id: str, settings: ProjectSettings, sample_story: str
) -> None:
    info = backend.create_project(user_id, sample_story, settings)
    url, _expires = backend.get_video_download_url(user_id, info.project_id)
    assert url.endswith("output.mp4")
    assert user_id in url
