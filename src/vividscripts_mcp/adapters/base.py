"""BackendProtocol — the contract every backend implementation must satisfy.

Decouples the MCP tool layer from any specific backend (VividScripts API, mock,
or any future implementation). Tools call protocol methods; the real backend
lives outside this package.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from vividscripts_mcp.models import (
    JobStatus,
    MusicSelection,
    ProjectDetail,
    ProjectInfo,
    ProjectSettings,
    ProjectSummary,
    PromptPayload,
    Scene,
    StepDefinition,
    StepResultOutcome,
    WorkflowState,
)


@runtime_checkable
class BackendProtocol(Protocol):
    """Backend contract for the MCP tool layer.

    All methods accept `user_id` so the backend can scope storage and
    permissions. The MCP server extracts user_id from the OAuth Bearer token
    before dispatching.
    """

    # --- Project management -------------------------------------------------

    def create_project(
        self, user_id: str, story: str, settings: ProjectSettings
    ) -> ProjectInfo: ...

    def list_projects(self, user_id: str) -> list[ProjectSummary]: ...

    def get_project(self, user_id: str, project_id: str) -> ProjectDetail: ...

    def delete_project(self, user_id: str, project_id: str) -> None: ...

    def duplicate_project(
        self, user_id: str, project_id: str, new_name: str | None = None
    ) -> ProjectInfo: ...

    # --- Workflow state -----------------------------------------------------

    def get_workflow_state(self, user_id: str, project_id: str) -> WorkflowState: ...

    def save_step_result(
        self,
        user_id: str,
        project_id: str,
        step_name: str,
        result: dict[str, Any],
        scene_index: int | None = None,
    ) -> StepResultOutcome:
        """Persist an AI step result.

        ``scene_index`` (KAN-90): ``None`` → the step is single-valued
        (``current_data[step] = result``, unchanged). ``>= 0`` → a
        per-scene/looped step; results accumulate under
        ``current_data[step]`` keyed by scene index. A step must be used
        consistently one way (mixed-mode is rejected).
        """
        ...

    def list_workflow_steps(self) -> list[StepDefinition]: ...

    # --- Prompts ------------------------------------------------------------

    def format_prompt(
        self, user_id: str, step_name: str, context: dict[str, Any]
    ) -> PromptPayload: ...

    def get_custom_prompt_override(self, user_id: str, step_name: str) -> str | None: ...

    def set_custom_prompt_override(self, user_id: str, step_name: str, template: str) -> None: ...

    # --- Media (async jobs) -------------------------------------------------

    def submit_job(
        self,
        user_id: str,
        project_id: str,
        job_type: str,
        params: dict[str, Any],
    ) -> str:
        """Submit an async job; return job_id."""
        ...

    def check_job(self, user_id: str, job_id: str) -> JobStatus: ...

    def select_music(self, user_id: str, project_id: str, mood: str) -> MusicSelection:
        """Choose a background-music mood for the project (synchronous).

        Not a job: records ``mood`` on the project and reports what the
        shared catalog already has. ``needs_generation`` is true when no
        tracks exist for the mood yet — the caller then runs the
        ``generate_music`` job.
        """
        ...

    # --- Scenes -------------------------------------------------------------

    def get_scenes(self, user_id: str, project_id: str) -> list[Scene]: ...

    def get_scene(self, user_id: str, project_id: str, scene_index: int) -> Scene: ...

    def update_scene(
        self,
        user_id: str,
        project_id: str,
        scene_index: int,
        fields: dict[str, Any],
    ) -> None: ...

    def add_scene(self, user_id: str, project_id: str, after_index: int, text: str) -> int:
        """Insert a scene; return its new index."""
        ...

    def remove_scene(self, user_id: str, project_id: str, scene_index: int) -> None: ...

    # --- URL handoff --------------------------------------------------------

    def mint_magic_link(
        self,
        user_id: str,
        project_id: str,
        view: str = "editor",
        ttl_seconds: int = 300,
    ) -> tuple[str, datetime]:
        """Return (url, expires_at)."""
        ...

    def get_video_download_url(self, user_id: str, project_id: str) -> tuple[str, datetime]:
        """Return (signed_url, expires_at)."""
        ...
