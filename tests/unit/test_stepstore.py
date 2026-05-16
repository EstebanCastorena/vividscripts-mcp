"""KAN-90 — per-scene step-result storage (stepstore + backend + tool)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from vividscripts_mcp.adapters.mock import MockBackend
from vividscripts_mcp.models import ProjectSettings
from vividscripts_mcp.oauth.bearer import UserClaims
from vividscripts_mcp.oauth.context import set_user_claims
from vividscripts_mcp.stepstore import store_step_result
from vividscripts_mcp.tools.state import make_save_step_result_tool

_U = "user-alpha"


# ---- pure helper -----------------------------------------------------------


def test_single_value_back_compat():
    cd: dict = {}
    assert store_step_result(cd, "title_generator", {"title": "X"}, None) is None
    assert cd == {"title_generator": {"title": "X"}}
    # overwrite (last-write-wins) still holds for single steps
    store_step_result(cd, "title_generator", {"title": "Y"}, None)
    assert cd["title_generator"] == {"title": "Y"}


def test_per_scene_accumulates():
    cd: dict = {}
    assert store_step_result(cd, "image_director_first", {"image_instruction": "a"}, 0) is None
    assert store_step_result(cd, "image_director_first", {"image_instruction": "b"}, 1) is None
    assert cd["image_director_first"] == {
        "0": {"image_instruction": "a"},
        "1": {"image_instruction": "b"},
    }


def test_mixed_mode_rejected_both_directions():
    cd: dict = {}
    store_step_result(cd, "s", {"a": 1}, None)
    assert "per-scene" in store_step_result(cd, "s", {"a": 2}, 0)  # single→per-scene

    cd2: dict = {}
    store_step_result(cd2, "s", {"a": 1}, 0)
    assert "single" in store_step_result(cd2, "s", {"a": 2}, None)  # per-scene→single


def test_negative_scene_index_rejected():
    assert store_step_result({}, "s", {"a": 1}, -1) == "scene_index must be >= 0"


# ---- MockBackend integration ----------------------------------------------


def _backend_with_project() -> tuple[MockBackend, str]:
    b = MockBackend(base_url="https://app.vividscripts.test")
    pid = b.create_project(user_id=_U, story="s", settings=ProjectSettings()).project_id
    return b, pid


def test_mock_single_unchanged():
    b, pid = _backend_with_project()
    out = b.save_step_result(_U, pid, "title_generator", {"title": "T"})
    assert out.success
    st = b.get_workflow_state(_U, pid)
    assert st.current_data["title_generator"] == {"title": "T"}


def test_mock_per_scene_and_mixed_guard():
    b, pid = _backend_with_project()
    assert b.save_step_result(
        _U, pid, "image_director_first", {"image_instruction": "a"}, scene_index=0
    ).success
    assert b.save_step_result(
        _U, pid, "image_director_first", {"image_instruction": "b"}, scene_index=1
    ).success
    st = b.get_workflow_state(_U, pid)
    assert st.current_data["image_director_first"] == {
        "0": {"image_instruction": "a"},
        "1": {"image_instruction": "b"},
    }
    bad = b.save_step_result(_U, pid, "image_director_first", {"image_instruction": "c"})
    assert not bad.success and bad.validation_errors


# ---- tool-level scene_index validation -------------------------------------


@pytest.fixture
def _auth() -> Iterator[None]:
    set_user_claims(UserClaims(sub=_U, client_id="c", scope=None, jti="j", exp=9999999999, iat=1))
    yield
    set_user_claims(None)


def test_tool_rejects_negative_scene_index(_auth: None):
    b, pid = _backend_with_project()
    tool = make_save_step_result_tool(b)
    out = tool(pid, "image_director_first", {"image_instruction": "a"}, -3)
    assert not out.success
    assert out.validation_errors == ["scene_index must be >= 0"]


def test_tool_passes_scene_index_through(_auth: None):
    b, pid = _backend_with_project()
    tool = make_save_step_result_tool(b)
    assert tool(pid, "image_director_first", {"image_instruction": "a"}, 2).success
    st = b.get_workflow_state(_U, pid)
    assert st.current_data["image_director_first"] == {"2": {"image_instruction": "a"}}
