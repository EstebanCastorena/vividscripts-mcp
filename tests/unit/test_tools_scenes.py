"""KAN-78 — scene-editing tools (get/update/add/remove)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from vividscripts_mcp.adapters.mock import MockBackend
from vividscripts_mcp.models import ProjectSettings, Scene
from vividscripts_mcp.oauth.bearer import UserClaims
from vividscripts_mcp.oauth.context import AuthRequired, set_user_claims
from vividscripts_mcp.tools.scenes import (
    AddSceneAck,
    SceneAck,
    make_add_scene_tool,
    make_get_scene_tool,
    make_get_scenes_tool,
    make_remove_scene_tool,
    make_update_scene_prompt_tool,
    make_update_scene_text_tool,
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


def test_add_get_update_remove_round_trip(backend, project_id, _auth):
    idx = make_add_scene_tool(backend)(project_id, after_index=-1, text="Scene zero")
    assert isinstance(idx, AddSceneAck)
    si = idx.new_scene_index

    scenes = make_get_scenes_tool(backend)(project_id)
    assert any(s.index == si and s.text == "Scene zero" for s in scenes)

    one = make_get_scene_tool(backend)(project_id, si)
    assert isinstance(one, Scene) and one.index == si

    assert make_update_scene_text_tool(backend)(project_id, si, "Edited").success
    assert make_get_scene_tool(backend)(project_id, si).text == "Edited"

    ack = make_update_scene_prompt_tool(backend)(project_id, si, "a moody forest")
    assert isinstance(ack, SceneAck) and ack.success
    assert make_get_scene_tool(backend)(project_id, si).image_prompt == "a moody forest"

    assert make_remove_scene_tool(backend)(project_id, si).success
    assert all(s.index != si for s in make_get_scenes_tool(backend)(project_id))


def test_get_scene_unknown_index_raises(backend, project_id, _auth):
    with pytest.raises(KeyError):
        make_get_scene_tool(backend)(project_id, 999)


def test_scene_tools_require_auth(backend, project_id):
    for factory in (
        lambda b: make_get_scenes_tool(b)(project_id),
        lambda b: make_get_scene_tool(b)(project_id, 0),
        lambda b: make_update_scene_text_tool(b)(project_id, 0, "x"),
        lambda b: make_update_scene_prompt_tool(b)(project_id, 0, "x"),
        lambda b: make_add_scene_tool(b)(project_id, 0, "x"),
        lambda b: make_remove_scene_tool(b)(project_id, 0),
    ):
        with pytest.raises(AuthRequired):
            factory(backend)


def test_cross_user_isolation(backend, project_id, _auth):
    set_user_claims(
        UserClaims(sub="user-beta", client_id="c", scope=None, jti="j2", exp=9999999999, iat=1)
    )
    try:
        with pytest.raises(KeyError):
            make_get_scenes_tool(backend)(project_id)
    finally:
        set_user_claims(None)
