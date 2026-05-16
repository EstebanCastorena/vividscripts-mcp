"""Media-generation MCP tools (Phase 4 / KAN-69+).

Long media operations (TTS, images, SFX, music, thumbnail, animation,
video compile) are **async jobs**: a ``generate_*`` tool returns a
``job_id`` immediately; the caller polls ``check_job`` until the status
is terminal. The backend owns the actual work (a background thread
running the WorkflowManager-independent ``MediaServices`` — KAN-68) and
the per-job persistence; these tools are thin, user-scoped wrappers
over ``BackendProtocol.submit_job`` / ``check_job``.

KAN-69 ships ``generate_audio`` + ``check_job`` and establishes the
pattern; KAN-70/71/72/73 add the remaining ``generate_*`` tools as
``job_type`` variants over the same machinery.
"""

from __future__ import annotations

from collections.abc import Callable

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from vividscripts_mcp.adapters.base import BackendProtocol
from vividscripts_mcp.models import JobStatus, MusicSelection
from vividscripts_mcp.oauth.context import require_user_claims


class JobSubmission(BaseModel):
    """Returned by every ``generate_*`` tool — the handle to poll."""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    job_type: str


def make_generate_audio_tool(
    backend: BackendProtocol,
) -> Callable[[str], JobSubmission]:
    """Build the ``generate_audio`` tool bound to ``backend``."""

    def generate_audio(project_id: str) -> JobSubmission:
        """Start TTS narration generation for every scene in the project.

        Returns immediately with a ``job_id`` — generation runs in the
        background. Poll ``check_job(job_id)`` until ``status`` is
        ``completed`` or ``failed``. The project must already have
        scenes (run the narration step first).

        When presenting the result, show one line:
        ``Audio job started: <job_id> — poll check_job for progress.``
        """
        user_id = require_user_claims().sub
        job_id = backend.submit_job(
            user_id=user_id,
            project_id=project_id,
            job_type="generate_audio",
            params={},
        )
        return JobSubmission(job_id=job_id, job_type="generate_audio")

    return generate_audio


def make_generate_images_tool(
    backend: BackendProtocol,
) -> Callable[[str], JobSubmission]:
    """Build the ``generate_images`` tool bound to ``backend`` (KAN-70)."""

    def generate_images(project_id: str) -> JobSubmission:
        """Start image generation for every scene in the project.

        Async — returns a ``job_id`` immediately; poll ``check_job``.
        The project must already have scenes with image directions.

        Present as one line:
        ``Image job started: <job_id> — poll check_job for progress.``
        """
        user_id = require_user_claims().sub
        job_id = backend.submit_job(
            user_id=user_id,
            project_id=project_id,
            job_type="generate_images",
            params={},
        )
        return JobSubmission(job_id=job_id, job_type="generate_images")

    return generate_images


def make_generate_sfx_tool(
    backend: BackendProtocol,
) -> Callable[[str], JobSubmission]:
    """Build the ``generate_sfx`` tool bound to ``backend`` (KAN-71)."""

    def generate_sfx(project_id: str) -> JobSubmission:
        """Start sound-effect analysis + generation for the project.

        Async — returns a ``job_id`` immediately; poll ``check_job``.

        Present as one line:
        ``SFX job started: <job_id> — poll check_job for progress.``
        """
        user_id = require_user_claims().sub
        job_id = backend.submit_job(
            user_id=user_id,
            project_id=project_id,
            job_type="generate_sfx",
            params={},
        )
        return JobSubmission(job_id=job_id, job_type="generate_sfx")

    return generate_sfx


def make_generate_thumbnail_tool(
    backend: BackendProtocol,
) -> Callable[[str], JobSubmission]:
    """Build the ``generate_thumbnail`` tool bound to ``backend`` (KAN-72)."""

    def generate_thumbnail(project_id: str) -> JobSubmission:
        """Start YouTube-thumbnail generation for the project.

        Async — returns a ``job_id`` immediately; poll ``check_job``.

        Present as one line:
        ``Thumbnail job started: <job_id> — poll check_job for progress.``
        """
        user_id = require_user_claims().sub
        job_id = backend.submit_job(
            user_id=user_id,
            project_id=project_id,
            job_type="generate_thumbnail",
            params={},
        )
        return JobSubmission(job_id=job_id, job_type="generate_thumbnail")

    return generate_thumbnail


def make_animate_scene_tool(
    backend: BackendProtocol,
) -> Callable[[str], JobSubmission]:
    """Build the ``animate_scene`` tool bound to ``backend`` (KAN-72)."""

    def animate_scene(project_id: str) -> JobSubmission:
        """Start image-to-video animation of the project's intro scenes.

        Async — returns a ``job_id`` immediately; poll ``check_job``.
        Requires generated images first.

        Present as one line:
        ``Animation job started: <job_id> — poll check_job for progress.``
        """
        user_id = require_user_claims().sub
        job_id = backend.submit_job(
            user_id=user_id,
            project_id=project_id,
            job_type="animate_scene",
            params={},
        )
        return JobSubmission(job_id=job_id, job_type="animate_scene")

    return animate_scene


def make_generate_music_tool(
    backend: BackendProtocol,
) -> Callable[[str], JobSubmission]:
    """Build the ``generate_music`` tool bound to ``backend`` (KAN-71)."""

    def generate_music(project_id: str) -> JobSubmission:
        """Start background-music synthesis for the project's mood.

        Async — returns a ``job_id`` immediately; poll ``check_job``.
        Requires a mood: call ``select_music`` first.

        Present as one line:
        ``Music job started: <job_id> — poll check_job for progress.``
        """
        user_id = require_user_claims().sub
        job_id = backend.submit_job(
            user_id=user_id,
            project_id=project_id,
            job_type="generate_music",
            params={},
        )
        return JobSubmission(job_id=job_id, job_type="generate_music")

    return generate_music


def make_select_music_tool(
    backend: BackendProtocol,
) -> Callable[[str, str], MusicSelection]:
    """Build the ``select_music`` tool bound to ``backend`` (KAN-71)."""

    def select_music(project_id: str, mood: str) -> MusicSelection:
        """Choose a background-music mood for the project (synchronous).

        Not a job — returns immediately. Records ``mood`` and reports
        the catalog tracks already available. If ``needs_generation``
        is true, run ``generate_music`` to synthesize tracks for it.
        """
        user_id = require_user_claims().sub
        return backend.select_music(user_id=user_id, project_id=project_id, mood=mood)

    return select_music


def make_check_job_tool(
    backend: BackendProtocol,
) -> Callable[[str], JobStatus]:
    """Build the ``check_job`` tool bound to ``backend``."""

    def check_job(job_id: str) -> JobStatus:
        """Poll an async media job.

        Returns ``{job_id, job_type, status, progress, result?, error?}``.
        ``status`` is one of ``queued|running|completed|failed``;
        ``progress`` is 0.0-1.0. On ``completed`` read ``result``; on
        ``failed`` read ``error``.
        """
        user_id = require_user_claims().sub
        return backend.check_job(user_id=user_id, job_id=job_id)

    return check_job


def register_media_tools(mcp: FastMCP, backend: BackendProtocol) -> None:
    """Register the Phase-4 media tools on the FastMCP server."""
    mcp.tool()(make_generate_audio_tool(backend))
    mcp.tool()(make_generate_images_tool(backend))
    mcp.tool()(make_generate_sfx_tool(backend))
    mcp.tool()(make_generate_thumbnail_tool(backend))
    mcp.tool()(make_animate_scene_tool(backend))
    mcp.tool()(make_generate_music_tool(backend))
    mcp.tool()(make_select_music_tool(backend))
    mcp.tool()(make_check_job_tool(backend))
