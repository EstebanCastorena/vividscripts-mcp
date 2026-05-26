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

# KAN-97 #9 — every value that ends up as a URL segment or filesystem
# component must match this. Restrictive on purpose: alphanumerics plus
# ``_`` and ``-`` is enough for human-readable names, and rules out
# ``../``, ``?``, ``@``, encoded slashes, and whitespace in one shot.
PROJECT_NAME_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"


class ProjectSettings(BaseModel):
    """Settings supplied when creating a project."""

    model_config = ConfigDict(extra="forbid")

    # KAN-97 #10 — style/music_mood are keys, not prose. Bound to keep an
    # unbounded string from reaching the prompt template / catalog lookup.
    style: str = Field(default="vintage_illustrated", description="Art style key", max_length=64)
    voice: Literal["male", "female"] = Field(default="male")
    dimension: Literal["landscape", "portrait"] = Field(default="landscape")
    music_mood: str | None = Field(
        default=None, description="Optional mood override", max_length=64
    )


class ProjectInfo(BaseModel):
    """Returned by create/duplicate — minimum a caller needs to act.

    No server-side filesystem path is exposed: it's an internal
    container detail, useless to a remote MCP client and a needless
    info leak. Callers act via ``project_id`` / ``editor_url``.
    """

    model_config = ConfigDict(extra="forbid")

    project_id: str
    project_name: str = Field(pattern=PROJECT_NAME_PATTERN)
    editor_url: str
    created_at: datetime


class ProjectSummary(BaseModel):
    """One row in list_projects."""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    project_name: str = Field(pattern=PROJECT_NAME_PATTERN)
    status: Literal["draft", "running", "compiled", "failed"]
    scene_count: int
    created_at: datetime
    editor_url: str
    video_url: str | None = None


class ProjectAssets(BaseModel):
    """Per-asset render status for the project (KAN-136).

    Flags whether each post-compile audio/visual layer is bound:

    - ``music``: a background-music track has been generated/selected
    - ``sfx``: sound effects have been rendered for the project
    - ``thumbnail``: the YouTube thumbnail PNG has been rendered
    - ``title_card``: a title-card asset has been rendered (KAN-131; always
      ``False`` until that ticket lands — kept for forward compatibility so
      callers can write today's verification code against the final shape)

    Drives the ``video_completeness`` rollup on :class:`ProjectDetail` and
    feeds the ``compile_video`` precondition check (KAN-130).
    """

    model_config = ConfigDict(extra="forbid")

    music: bool = False
    sfx: bool = False
    thumbnail: bool = False
    title_card: bool = False


VideoCompleteness = Literal["complete", "partial", "minimal"]


class ProjectDetail(BaseModel):
    """Full project view for get_project."""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    project_name: str = Field(pattern=PROJECT_NAME_PATTERN)
    metadata: dict[str, Any]
    scene_summaries: list[dict[str, Any]]
    video_status: Literal["none", "compiling", "ready", "failed"]
    # KAN-136 — agents can't verify completeness from ``video_status`` alone
    # (it's ``ready`` even when SFX/music aren't bound). ``assets`` and
    # ``video_completeness`` give a programmatic completeness signal so the
    # ``generate_video_end_to_end`` orchestrator (KAN-133) and the
    # ``compile_video`` precondition check (KAN-130) don't have to listen
    # to the mp4 to know what shipped.
    assets: ProjectAssets = Field(default_factory=ProjectAssets)
    video_completeness: VideoCompleteness = Field(
        default="minimal",
        description=(
            "Rollup of supported renderable asset layers. 'complete' = every "
            "renderable layer bound (today: music + sfx + thumbnail; title_card "
            "joins the rollup when KAN-131 lands). 'partial' = at least one but "
            "not all. 'minimal' = none — the compile shipped narration + scene "
            "images only."
        ),
    )
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


class CompileReadiness(BaseModel):
    """Returned by ``BackendProtocol.check_compile_readiness`` (KAN-130).

    Tells the ``compile_video`` tool whether every optional audio/visual
    layer the pipeline expects is bound before it kicks off the FFmpeg
    assembly. ``ready`` is just the convenience rollup
    (``not missing and not running``).

    ``missing`` and ``running`` carry asset-class names (``"music"``,
    ``"sfx"``, ``"thumbnail"``) rather than ``generate_*`` job-type strings
    so the tool layer can present them to callers without leaking the
    job-type vocabulary. ``title_card`` is excluded until KAN-131 ships
    the renderer.
    """

    model_config = ConfigDict(extra="forbid")

    ready: bool
    missing: list[str] = Field(default_factory=list)
    running: list[str] = Field(default_factory=list)


class MusicSelection(BaseModel):
    """Returned by select_music (a synchronous catalog lookup).

    ``select_music`` does not generate anything — it records the chosen
    mood for the project and reports what the shared music catalog
    already has. If ``needs_generation`` is true the caller should run
    the ``generate_music`` job to synthesize tracks for the mood.
    """

    model_config = ConfigDict(extra="forbid")

    mood: str
    available_tracks: list[str]
    needs_generation: bool
