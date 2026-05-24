"""Audit finding #10 — bounded free-text inputs to tools + models.

``story``, ``new_text``, ``new_prompt``, ``template`` were bare ``str``
on the MCP tool surface — a remote caller could submit a gigabyte
payload, blow up memory before any backend logic looked at it. Same
shape for ``ProjectSettings.style`` and ``music_mood``, which the audit
flagged as flowing into prompt templates and the music-catalog lookup.

Enforced bounds (chosen to be generous for real use, tight for attack):

- ``story`` ≤ 200_000 chars (≈ a long novel chapter)
- ``new_text`` ≤ 10_000 chars (one scene's narration)
- ``new_prompt`` ≤ 10_000 chars (one image prompt)
- ``template`` ≤ 50_000 chars (a custom prompt template)
- ``ProjectSettings.style`` ≤ 64 chars (it's a key, not prose)
- ``ProjectSettings.music_mood`` ≤ 64 chars (also a key)
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from pydantic import ValidationError

from vividscripts_mcp.adapters.mock import MockBackend
from vividscripts_mcp.models import ProjectSettings
from vividscripts_mcp.oauth.bearer import UserClaims
from vividscripts_mcp.oauth.context import set_user_claims
from vividscripts_mcp.prompts import PROMPT_INTERFACES
from vividscripts_mcp.tools.projects import make_create_project_tool
from vividscripts_mcp.tools.scenes import (
    make_add_scene_tool,
    make_update_scene_prompt_tool,
    make_update_scene_text_tool,
)
from vividscripts_mcp.tools.state import make_set_custom_prompt_override_tool


@pytest.fixture
def backend() -> MockBackend:
    return MockBackend(base_url="https://app.vividscripts.test")


@pytest.fixture
def project_id(backend: MockBackend) -> str:
    return backend.create_project(
        user_id="user-alpha", story="A short story.", settings=ProjectSettings()
    ).project_id


@pytest.fixture
def _auth() -> Iterator[None]:
    set_user_claims(
        UserClaims(
            sub="user-alpha",
            client_id="c",
            scope=None,
            jti="j-bounds",
            exp=9999999999,
            iat=1,
        )
    )
    yield
    set_user_claims(None)


# ---------------------------------------------------------------------------
# create_project(story=...)
# ---------------------------------------------------------------------------


def test_create_project_rejects_oversize_story(backend: MockBackend, _auth: None) -> None:
    tool = make_create_project_tool(backend)
    with pytest.raises(ValueError, match="story"):
        tool("x" * 200_001, ProjectSettings())


def test_create_project_accepts_long_but_bounded_story(backend: MockBackend, _auth: None) -> None:
    tool = make_create_project_tool(backend)
    info = tool("x" * 200_000, ProjectSettings())
    assert info.project_id


# ---------------------------------------------------------------------------
# update_scene_text / update_scene_prompt / add_scene
# ---------------------------------------------------------------------------


def test_update_scene_text_rejects_oversize(
    backend: MockBackend, project_id: str, _auth: None
) -> None:
    tool = make_update_scene_text_tool(backend)
    with pytest.raises(ValueError, match="new_text"):
        tool(project_id, 0, "x" * 10_001)


def test_update_scene_prompt_rejects_oversize(
    backend: MockBackend, project_id: str, _auth: None
) -> None:
    tool = make_update_scene_prompt_tool(backend)
    with pytest.raises(ValueError, match="new_prompt"):
        tool(project_id, 0, "x" * 10_001)


def test_add_scene_rejects_oversize_text(
    backend: MockBackend, project_id: str, _auth: None
) -> None:
    tool = make_add_scene_tool(backend)
    with pytest.raises(ValueError, match="text"):
        tool(project_id, -1, "x" * 10_001)


# ---------------------------------------------------------------------------
# set_custom_prompt_override(template=...)
# ---------------------------------------------------------------------------


def test_set_custom_prompt_override_rejects_oversize_template(
    backend: MockBackend, _auth: None
) -> None:
    tool = make_set_custom_prompt_override_tool(backend)
    step_name = next(iter(PROMPT_INTERFACES))
    with pytest.raises(ValueError, match="template"):
        tool(step_name, "x" * 50_001)


# ---------------------------------------------------------------------------
# ProjectSettings — Pydantic-level model bounds
# ---------------------------------------------------------------------------


def test_project_settings_rejects_oversize_style() -> None:
    with pytest.raises(ValidationError):
        ProjectSettings(style="x" * 65)


def test_project_settings_rejects_oversize_music_mood() -> None:
    with pytest.raises(ValidationError):
        ProjectSettings(music_mood="x" * 65)


def test_project_settings_accepts_bounded_values() -> None:
    settings = ProjectSettings(style="dark_cinematic", music_mood="dark-tension")
    assert settings.style == "dark_cinematic"
    assert settings.music_mood == "dark-tension"
