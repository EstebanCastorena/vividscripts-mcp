"""Pydantic models for the MCP tool surface.

These are the typed payloads that flow between MCP tools and the backend.
Every model has a JSON schema that the MCP server exposes to clients.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

StepStatus = Literal["pending", "running", "completed", "failed", "skipped"]
JobStatusLiteral = Literal["queued", "running", "completed", "failed"]


class ProjectSettings(BaseModel):
    """Settings supplied when creating a project."""

    model_config = ConfigDict(extra="forbid")

    style: str = Field(default="vintage_illustrated", description="Art style key")
    voice: Literal["male", "female"] = Field(default="male")
    dimension: Literal["landscape", "portrait"] = Field(default="landscape")
    music_mood: str | None = Field(default=None, description="Optional mood override")


class ProjectInfo(BaseModel):
    """Returned by create/duplicate — minimum a caller needs to act."""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    project_name: str
    project_path: str = Field(description="Server-side path, opaque to clients")
    editor_url: str
    created_at: datetime


class ProjectSummary(BaseModel):
    """One row in list_projects."""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    project_name: str
    status: Literal["draft", "running", "compiled", "failed"]
    scene_count: int
    created_at: datetime
    editor_url: str
    video_url: str | None = None


class ProjectDetail(BaseModel):
    """Full project view for get_project."""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    project_name: str
    metadata: dict[str, Any]
    scene_summaries: list[dict[str, Any]]
    video_status: Literal["none", "compiling", "ready", "failed"]
    blueprint_summary: dict[str, Any] | None = None
    editor_url: str


class StepDefinition(BaseModel):
    """One row in list_workflow_steps."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    ai_required: bool
    depends_on: list[str]
    loops_over: Literal["story", "paragraph", "scene", "segment"] | None = None


class WorkflowState(BaseModel):
    """Returned by get_workflow_state."""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    status: Literal["not_started", "in_progress", "completed", "failed"]
    completed_steps: list[str]
    current_step: str | None
    current_data: dict[str, Any] = Field(
        default_factory=dict,
        description="Accumulated state: blueprint, scenes[], bibles, etc.",
    )


class StepResultOutcome(BaseModel):
    """Returned by save_step_result."""

    model_config = ConfigDict(extra="forbid")

    success: bool
    validation_errors: list[str] | None = None
    next_step: str | None = None


class PromptPayload(BaseModel):
    """Returned by format_prompt — what Claude Code needs to process the step."""

    model_config = ConfigDict(extra="forbid")

    step_name: str
    prompt: str
    system_prompt: str | None = None
    output_schema: dict[str, Any]
    instructions: str = Field(
        default="",
        description="Hints for Claude about how to interpret/return the output",
    )


class JobStatus(BaseModel):
    """Returned by check_job."""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    job_type: str
    status: JobStatusLiteral
    progress: float = Field(ge=0.0, le=1.0, default=0.0)
    result: dict[str, Any] | None = None
    error: str | None = None


class Scene(BaseModel):
    """Returned by get_scenes / get_scene."""

    model_config = ConfigDict(extra="forbid")

    index: int
    text: str
    image_url: str | None = None
    audio_url: str | None = None
    image_prompt: str | None = None
    visual_subject: str | None = None
    duration_seconds: float | None = None


class MagicLinkUrl(BaseModel):
    """Returned by mint_magic_link."""

    model_config = ConfigDict(extra="forbid")

    url: str
    expires_at: datetime
