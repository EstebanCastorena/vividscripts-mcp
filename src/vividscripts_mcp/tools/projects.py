"""Project management MCP tools — KAN-53.

Three tools, all user-scoped via the Bearer claims contextvar:

- ``create_project`` — accepts a story + settings, returns the
  freshly-created project's identifier + editor URL.
- ``list_projects`` — returns the caller's projects only.
- ``get_project`` — returns full detail for one project, owned by the
  caller.

Cross-user isolation is enforced at the backend layer
(:class:`MockBackend` raises :class:`KeyError` when a project isn't
owned by the requesting user). The tool functions don't catch this —
they let the exception surface, and FastMCP renders it as an error
response on the wire.
"""

from __future__ import annotations

from collections.abc import Callable

from vividscripts_mcp.adapters.base import BackendProtocol
from vividscripts_mcp.models import (
    ProjectDetail,
    ProjectInfo,
    ProjectSettings,
    ProjectSummary,
)
from vividscripts_mcp.oauth.context import require_user_claims


def make_create_project_tool(
    backend: BackendProtocol,
) -> Callable[[str, ProjectSettings], ProjectInfo]:
    """Build the ``create_project`` MCP tool bound to ``backend``."""

    def create_project(story: str, settings: ProjectSettings) -> ProjectInfo:
        """Create a new VividScripts project from a story + settings.

        Returns the project id and an editor URL to open it.

        When presenting this result to a user, show it as two plain
        lines — not a table — so the link stays on one line and is
        easy to click::

            Project ID: <project_id>
            Editor: <editor_url>
        """
        user_id = require_user_claims().sub
        return backend.create_project(user_id=user_id, story=story, settings=settings)

    return create_project


def make_list_projects_tool(
    backend: BackendProtocol,
) -> Callable[[], list[ProjectSummary]]:
    """Build the ``list_projects`` MCP tool bound to ``backend``."""

    def list_projects() -> list[ProjectSummary]:
        """List projects owned by the authenticated user."""
        user_id = require_user_claims().sub
        return backend.list_projects(user_id=user_id)

    return list_projects


def make_get_project_tool(
    backend: BackendProtocol,
) -> Callable[[str], ProjectDetail]:
    """Build the ``get_project`` MCP tool bound to ``backend``."""

    def get_project(project_id: str) -> ProjectDetail:
        """Fetch the full detail of one project.

        Raises ``KeyError`` (surfaced by FastMCP as an error response)
        when the project doesn't exist or isn't owned by the caller.
        """
        user_id = require_user_claims().sub
        return backend.get_project(user_id=user_id, project_id=project_id)

    return get_project
