"""In-memory MockBackend.

Implements `BackendProtocol` against in-process dicts. Used for tests, demos,
and the public quickstart so reviewers can run the MCP server without a
VividScripts account.

Behavior is deliberately deterministic where possible: project IDs are
generated from a counter (not random) so test assertions stay simple.
"""

from __future__ import annotations

import secrets
import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar, Literal

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
from vividscripts_mcp.stepstore import store_step_result

# Mirrors the 16-step pipeline from VividScripts. The MockBackend uses this to
# advance workflow state; the real backend will return its own definitive list.
WORKFLOW_STEPS: list[StepDefinition] = [
    StepDefinition(
        name="project_setup",
        description="Create folder structure",
        ai_required=False,
        depends_on=[],
    ),
    StepDefinition(
        name="story_blueprint",
        description="Analyze story for creative intelligence",
        ai_required=True,
        depends_on=["project_setup"],
        loops_over="story",
    ),
    StepDefinition(
        name="music_selection",
        description="Pick/generate background music by mood",
        ai_required=False,
        depends_on=["story_blueprint"],
    ),
    StepDefinition(
        name="narration_grouping",
        description="Split story into visual scenes",
        ai_required=True,
        depends_on=["story_blueprint"],
        loops_over="paragraph",
    ),
    StepDefinition(
        name="story_summarizer",
        description="Create spoiler-free hook brief",
        ai_required=True,
        depends_on=["narration_grouping"],
        loops_over="story",
    ),
    StepDefinition(
        name="title_generator",
        description="YouTube-optimized title",
        ai_required=True,
        depends_on=["story_summarizer"],
        loops_over="story",
    ),
    StepDefinition(
        name="short_title_generator",
        description="2-3 word project name; renames folder",
        ai_required=True,
        depends_on=["title_generator"],
        loops_over="story",
    ),
    StepDefinition(
        name="audio_generation",
        description="TTS narration + Whisper timestamps",
        ai_required=False,
        depends_on=["short_title_generator"],
        loops_over="scene",
    ),
    StepDefinition(
        name="stage_direction",
        description="Character and location consistency data + per-scene context",
        ai_required=True,
        depends_on=["audio_generation"],
        loops_over="scene",
    ),
    StepDefinition(
        name="image_split_analyzer",
        description="Decide 1-3 images per scene",
        ai_required=True,
        depends_on=["stage_direction"],
        loops_over="scene",
    ),
    StepDefinition(
        name="image_direction",
        description="Image prompts with character consistency and composition",
        ai_required=True,
        depends_on=["image_split_analyzer"],
        loops_over="segment",
    ),
    StepDefinition(
        name="sound_effect_analysis",
        description="SFX selection + timing",
        ai_required=True,
        depends_on=["image_direction"],
        loops_over="scene",
    ),
    StepDefinition(
        name="image_generation",
        description="Run image generation against the picked provider",
        ai_required=False,
        depends_on=["image_direction"],
        loops_over="segment",
    ),
    StepDefinition(
        name="video_animation",
        description="Optional Kling animation for intro scenes",
        ai_required=True,
        depends_on=["image_generation"],
        loops_over="scene",
    ),
    StepDefinition(
        name="thumbnail",
        description="Thumbnail image + overlay text",
        ai_required=True,
        depends_on=["image_generation"],
        loops_over="story",
    ),
    StepDefinition(
        name="video_compilation",
        description="FFmpeg final assembly",
        ai_required=False,
        depends_on=["thumbnail", "sound_effect_analysis"],
        loops_over="story",
    ),
]


class _ProjectState:
    """Mock's per-project bag of state."""

    def __init__(self, project_id: str, project_name: str, settings: ProjectSettings, story: str):
        self.project_id = project_id
        self.project_name = project_name
        self.settings = settings
        self.story = story
        self.created_at = datetime.now(UTC)
        self.completed_steps: list[str] = []
        self.current_data: dict[str, Any] = {"story": story, "settings": settings.model_dump()}
        self.scenes: list[Scene] = []
        self.video_status: str = "none"


class MockBackend:
    """In-memory backend. Thread-safe via a coarse lock."""

    def __init__(self, base_url: str = "http://localhost:5050") -> None:
        self._base_url = base_url.rstrip("/")
        self._lock = threading.RLock()
        self._projects: dict[tuple[str, str], _ProjectState] = {}
        self._project_counter = 0
        self._jobs: dict[str, JobStatus] = {}
        self._custom_prompts: dict[tuple[str, str], str] = {}

    # ----- helpers ----------------------------------------------------------

    def _editor_url(self, project_name: str) -> str:
        return f"{self._base_url}/studio?project={project_name}"

    def _require(self, user_id: str, project_id: str) -> _ProjectState:
        state = self._projects.get((user_id, project_id))
        if state is None:
            # KAN-98 #18 — do not echo the caller's own ``user_id``
            # (OAuth ``sub``) into the wire / model-visible error string.
            # Existence vs. ownership are correctly indistinguishable
            # since both code paths land here; the ``sub`` echo was a
            # gratuitous PII leak.
            msg = f"project {project_id!r} not found"
            raise KeyError(msg)
        return state

    # ----- project management ----------------------------------------------

    def create_project(self, user_id: str, story: str, settings: ProjectSettings) -> ProjectInfo:
        with self._lock:
            self._project_counter += 1
            project_id = f"mock-{self._project_counter:04d}"
            project_name = f"Untitled_Project_{self._project_counter}"
            state = _ProjectState(project_id, project_name, settings, story)
            self._projects[(user_id, project_id)] = state
            return ProjectInfo(
                project_id=project_id,
                project_name=project_name,
                editor_url=self._editor_url(project_name),
                created_at=state.created_at,
            )

    def list_projects(self, user_id: str) -> list[ProjectSummary]:
        with self._lock:
            results: list[ProjectSummary] = []
            for (uid, _pid), state in self._projects.items():
                if uid != user_id:
                    continue
                results.append(
                    ProjectSummary(
                        project_id=state.project_id,
                        project_name=state.project_name,
                        status=self._compute_status(state),
                        scene_count=len(state.scenes),
                        created_at=state.created_at,
                        editor_url=self._editor_url(state.project_name),
                        video_url=None,
                    )
                )
            return results

    def get_project(self, user_id: str, project_id: str) -> ProjectDetail:
        with self._lock:
            state = self._require(user_id, project_id)
            return ProjectDetail(
                project_id=state.project_id,
                project_name=state.project_name,
                metadata={"settings": state.settings.model_dump()},
                scene_summaries=[{"index": s.index, "text": s.text[:80]} for s in state.scenes],
                video_status="ready" if state.video_status == "ready" else "none",
                blueprint_summary=state.current_data.get("blueprint"),
                editor_url=self._editor_url(state.project_name),
            )

    def delete_project(self, user_id: str, project_id: str) -> None:
        with self._lock:
            key = (user_id, project_id)
            if key in self._projects:
                del self._projects[key]

    def duplicate_project(
        self, user_id: str, project_id: str, new_name: str | None = None
    ) -> ProjectInfo:
        with self._lock:
            src = self._require(user_id, project_id)
            self._project_counter += 1
            new_id = f"mock-{self._project_counter:04d}"
            name = new_name or f"{src.project_name}_copy"
            dup = _ProjectState(new_id, name, src.settings, src.story)
            dup.completed_steps = list(src.completed_steps)
            dup.current_data = dict(src.current_data)
            dup.scenes = list(src.scenes)
            self._projects[(user_id, new_id)] = dup
            return ProjectInfo(
                project_id=new_id,
                project_name=name,
                editor_url=self._editor_url(name),
                created_at=dup.created_at,
            )

    # ----- workflow state ---------------------------------------------------

    def get_workflow_state(self, user_id: str, project_id: str) -> WorkflowState:
        with self._lock:
            state = self._require(user_id, project_id)
            return WorkflowState(
                project_id=project_id,
                status=self._compute_workflow_status(state),
                completed_steps=list(state.completed_steps),
                current_step=self._next_step(state),
                current_data=dict(state.current_data),
            )

    def save_step_result(
        self,
        user_id: str,
        project_id: str,
        step_name: str,
        result: dict[str, Any],
        scene_index: int | None = None,
    ) -> StepResultOutcome:
        with self._lock:
            state = self._require(user_id, project_id)
            # KAN-59: the save_step_result *tool* validates step_name +
            # result against the canonical JSON schema before reaching
            # the backend, so the mock only guards against an empty name.
            if not step_name.strip():
                return StepResultOutcome(
                    success=False,
                    validation_errors=["step_name must be non-empty"],
                )
            err = store_step_result(state.current_data, step_name, result, scene_index)
            if err is not None:
                return StepResultOutcome(success=False, validation_errors=[err])
            if step_name not in state.completed_steps:
                state.completed_steps.append(step_name)
            return StepResultOutcome(success=True, next_step=self._next_step(state))

    def list_workflow_steps(self) -> list[StepDefinition]:
        return list(WORKFLOW_STEPS)

    # ----- prompts ----------------------------------------------------------

    def format_prompt(self, user_id: str, step_name: str, context: dict[str, Any]) -> PromptPayload:
        """Mock returns a stub prompt — the real backend renders from templates."""
        template = self._custom_prompts.get((user_id, step_name))
        prompt = template or f"[MOCK PROMPT for {step_name}] context_keys={list(context)}"
        return PromptPayload(
            step_name=step_name,
            prompt=prompt,
            output_schema={"type": "object"},
            instructions="MockBackend prompt — replace with real templates in production.",
        )

    def get_custom_prompt_override(self, user_id: str, step_name: str) -> str | None:
        return self._custom_prompts.get((user_id, step_name))

    def set_custom_prompt_override(self, user_id: str, step_name: str, template: str) -> None:
        with self._lock:
            self._custom_prompts[(user_id, step_name)] = template

    # ----- media jobs -------------------------------------------------------

    def submit_job(
        self,
        user_id: str,
        project_id: str,
        job_type: str,
        params: dict[str, Any],
    ) -> str:
        with self._lock:
            self._require(user_id, project_id)
            job_id = str(uuid.uuid4())
            self._jobs[job_id] = JobStatus(
                job_id=job_id,
                job_type=job_type,
                status="completed",
                progress=1.0,
                result={"mock": True, "job_type": job_type, "params": params},
            )
            return job_id

    def check_job(self, user_id: str, job_id: str) -> JobStatus:
        job = self._jobs.get(job_id)
        if job is None:
            msg = f"job {job_id!r} not found"
            raise KeyError(msg)
        return job

    #: Stand-in for the shared music catalog (the real adapter reads
    #: assets/music/music-catalog.json). Only "dark-tension" ships with
    #: tracks; any other mood reports needs_generation=True.
    _MUSIC_CATALOG: ClassVar[dict[str, list[str]]] = {
        "dark-tension": ["horror-394969.mp3", "scary-ambience-347437.mp3"],
    }

    def select_music(self, user_id: str, project_id: str, mood: str) -> MusicSelection:
        with self._lock:
            state = self._require(user_id, project_id)
            if not mood:
                msg = "mood must be a non-empty string"
                raise ValueError(msg)
            state.settings = state.settings.model_copy(update={"music_mood": mood})
            tracks = self._MUSIC_CATALOG.get(mood, [])
            return MusicSelection(
                mood=mood,
                available_tracks=list(tracks),
                needs_generation=not tracks,
            )

    # ----- scenes -----------------------------------------------------------

    def get_scenes(self, user_id: str, project_id: str) -> list[Scene]:
        with self._lock:
            return list(self._require(user_id, project_id).scenes)

    def get_scene(self, user_id: str, project_id: str, scene_index: int) -> Scene:
        with self._lock:
            state = self._require(user_id, project_id)
            for scene in state.scenes:
                if scene.index == scene_index:
                    return scene
            msg = f"scene {scene_index} not found in project {project_id!r}"
            raise KeyError(msg)

    def update_scene(
        self,
        user_id: str,
        project_id: str,
        scene_index: int,
        fields: dict[str, Any],
    ) -> None:
        with self._lock:
            state = self._require(user_id, project_id)
            for i, scene in enumerate(state.scenes):
                if scene.index == scene_index:
                    updated = scene.model_copy(update=fields)
                    state.scenes[i] = updated
                    return
            msg = f"scene {scene_index} not found in project {project_id!r}"
            raise KeyError(msg)

    def add_scene(self, user_id: str, project_id: str, after_index: int, text: str) -> int:
        with self._lock:
            state = self._require(user_id, project_id)
            new_index = max((s.index for s in state.scenes), default=-1) + 1
            state.scenes.append(Scene(index=new_index, text=text))
            state.scenes.sort(key=lambda s: s.index)
            return new_index

    def remove_scene(self, user_id: str, project_id: str, scene_index: int) -> None:
        with self._lock:
            state = self._require(user_id, project_id)
            state.scenes = [s for s in state.scenes if s.index != scene_index]

    # ----- URLs -------------------------------------------------------------

    def mint_magic_link(
        self,
        user_id: str,
        project_id: str,
        view: str = "editor",
        ttl_seconds: int = 300,
    ) -> tuple[str, datetime]:
        with self._lock:
            self._require(user_id, project_id)
            # KAN-97 #9 — opaque server-generated token, no user_id PII in
            # the URL and no user-controlled ``project_name`` raw-
            # interpolated. The token is the only thing the redemption
            # endpoint resolves back to (user, project, expiry). ``view``
            # is kept in the query string but bounded by the tool layer
            # to ``{editor, video}``.
            token = secrets.token_urlsafe(24)
            expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
            url = f"{self._base_url}/m/{token}?view={view}"
            return url, expires_at

    def get_video_download_url(self, user_id: str, project_id: str) -> tuple[str, datetime]:
        with self._lock:
            self._require(user_id, project_id)
            # KAN-97 #9 — opaque token, no user_id (OAuth ``sub``) in the
            # URL path. Two consecutive calls produce different tokens so
            # the URL genuinely expires at ``expires_at`` rather than
            # silently aliasing to a stable structure-based path.
            token = secrets.token_urlsafe(24)
            expires_at = datetime.now(UTC) + timedelta(hours=1)
            url = f"{self._base_url}/v/{token}/output.mp4"
            return url, expires_at

    # ----- private helpers --------------------------------------------------

    def _next_step(self, state: _ProjectState) -> str | None:
        for step in WORKFLOW_STEPS:
            if step.name not in state.completed_steps:
                return step.name
        return None

    def _compute_status(
        self, state: _ProjectState
    ) -> Literal["draft", "running", "compiled", "failed"]:
        if not state.completed_steps:
            return "draft"
        if "video_compilation" in state.completed_steps:
            return "compiled"
        return "running"

    def _compute_workflow_status(
        self, state: _ProjectState
    ) -> Literal["not_started", "in_progress", "completed", "failed"]:
        if not state.completed_steps:
            return "not_started"
        if len(state.completed_steps) == len(WORKFLOW_STEPS):
            return "completed"
        return "in_progress"
