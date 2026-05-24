"""Scene-editing MCP tools (Phase 5 / KAN-78).

Bidirectional with the VividScripts web editor: every mutation goes
through the backend onto the *same* on-disk scene representation the
editor reads, so an edit made by Claude Code shows up in the editor on
refresh and vice-versa.

- ``get_scenes`` / ``get_scene`` — read.
- ``update_scene_prompt`` / ``update_scene_text`` — edit one field.
- ``add_scene`` / ``remove_scene`` — structural (re-indexes downstream).

All user-scoped via the Bearer claims contextvar; cross-user access
surfaces as the backend's ``KeyError`` (rendered as an error on the
wire), same as the other tool groups.
"""

from __future__ import annotations

from collections.abc import Callable

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from vividscripts_mcp.adapters.base import BackendProtocol
from vividscripts_mcp.models import Scene
from vividscripts_mcp.oauth.context import require_user_claims

# KAN-97 #10 — bound the free-text fields. A scene's narration and a
# single image prompt both comfortably fit in 10_000 chars; the cap
# refuses memory-exhaustion payloads without constraining real edits.
_MAX_SCENE_TEXT_CHARS = 10_000
_MAX_SCENE_PROMPT_CHARS = 10_000


class SceneAck(BaseModel):
    """Returned by update/remove scene tools."""

    model_config = ConfigDict(extra="forbid")

    success: bool


class AddSceneAck(BaseModel):
    """Returned by add_scene."""

    model_config = ConfigDict(extra="forbid")

    new_scene_index: int


def make_get_scenes_tool(
    backend: BackendProtocol,
) -> Callable[[str], list[Scene]]:
    """Build ``get_scenes`` bound to ``backend``."""

    def get_scenes(project_id: str) -> list[Scene]:
        """List every scene in the project (index, text, media URLs,
        image prompt). Reflects edits made in the web editor."""
        user_id = require_user_claims().sub
        return backend.get_scenes(user_id=user_id, project_id=project_id)

    return get_scenes


def make_get_scene_tool(
    backend: BackendProtocol,
) -> Callable[[str, int], Scene]:
    """Build ``get_scene`` bound to ``backend``."""

    def get_scene(project_id: str, scene_index: int) -> Scene:
        """Return one scene's full data by 0-based index."""
        user_id = require_user_claims().sub
        return backend.get_scene(user_id=user_id, project_id=project_id, scene_index=scene_index)

    return get_scene


def make_update_scene_prompt_tool(
    backend: BackendProtocol,
) -> Callable[[str, int, str], SceneAck]:
    """Build ``update_scene_prompt`` bound to ``backend``."""

    def update_scene_prompt(project_id: str, scene_index: int, new_prompt: str) -> SceneAck:
        """Replace a scene's image prompt. Visible in the web editor on
        refresh; run ``regenerate_scene_image`` to re-render."""
        if len(new_prompt) > _MAX_SCENE_PROMPT_CHARS:
            raise ValueError(
                f"new_prompt is {len(new_prompt)} chars; max is {_MAX_SCENE_PROMPT_CHARS}"
            )
        user_id = require_user_claims().sub
        backend.update_scene(
            user_id=user_id,
            project_id=project_id,
            scene_index=scene_index,
            fields={"image_prompt": new_prompt},
        )
        return SceneAck(success=True)

    return update_scene_prompt


def make_update_scene_text_tool(
    backend: BackendProtocol,
) -> Callable[[str, int, str], SceneAck]:
    """Build ``update_scene_text`` bound to ``backend``."""

    def update_scene_text(project_id: str, scene_index: int, new_text: str) -> SceneAck:
        """Replace a scene's narration text. Run
        ``regenerate_scene_audio`` to re-synthesize."""
        if len(new_text) > _MAX_SCENE_TEXT_CHARS:
            raise ValueError(f"new_text is {len(new_text)} chars; max is {_MAX_SCENE_TEXT_CHARS}")
        user_id = require_user_claims().sub
        backend.update_scene(
            user_id=user_id,
            project_id=project_id,
            scene_index=scene_index,
            fields={"text": new_text},
        )
        return SceneAck(success=True)

    return update_scene_text


def make_add_scene_tool(
    backend: BackendProtocol,
) -> Callable[[str, int, str], AddSceneAck]:
    """Build ``add_scene`` bound to ``backend``."""

    def add_scene(project_id: str, after_index: int, text: str) -> AddSceneAck:
        """Insert a new scene after ``after_index`` (0-based) with the
        given narration text. Downstream scenes are re-indexed."""
        if len(text) > _MAX_SCENE_TEXT_CHARS:
            raise ValueError(f"text is {len(text)} chars; max is {_MAX_SCENE_TEXT_CHARS}")
        user_id = require_user_claims().sub
        new_index = backend.add_scene(
            user_id=user_id,
            project_id=project_id,
            after_index=after_index,
            text=text,
        )
        return AddSceneAck(new_scene_index=new_index)

    return add_scene


def make_remove_scene_tool(
    backend: BackendProtocol,
) -> Callable[[str, int], SceneAck]:
    """Build ``remove_scene`` bound to ``backend``."""

    def remove_scene(project_id: str, scene_index: int) -> SceneAck:
        """Delete a scene by 0-based index. Downstream scenes are
        re-indexed; refuses to remove the last remaining scene."""
        user_id = require_user_claims().sub
        backend.remove_scene(user_id=user_id, project_id=project_id, scene_index=scene_index)
        return SceneAck(success=True)

    return remove_scene


def register_scene_tools(mcp: FastMCP, backend: BackendProtocol) -> None:
    """Register the Phase-5 scene-editing tools on the FastMCP server."""
    mcp.tool()(make_get_scenes_tool(backend))
    mcp.tool()(make_get_scene_tool(backend))
    mcp.tool()(make_update_scene_prompt_tool(backend))
    mcp.tool()(make_update_scene_text_tool(backend))
    mcp.tool()(make_add_scene_tool(backend))
    mcp.tool()(make_remove_scene_tool(backend))
