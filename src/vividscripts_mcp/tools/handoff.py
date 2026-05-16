"""URL-handoff MCP tools (Phase 5 / KAN-77).

The Vercel-style "here's your URL" surface:

- ``mint_magic_link`` — a short-lived signed URL that opens the project
  in the editor (or the video player) with no second login.
- ``get_video_download_url`` — a short-lived signed URL to the compiled
  video.

Both are thin, user-scoped wrappers over ``BackendProtocol``; the
actual JWT signing/verification + the redemption route live on the
VividScripts side (``cognito_auth`` — KAN-74/75). The backend returns
``(url, expires_at)``; these tools shape it into ``MagicLinkUrl``.
"""

from __future__ import annotations

from collections.abc import Callable

from mcp.server.fastmcp import FastMCP

from vividscripts_mcp.adapters.base import BackendProtocol
from vividscripts_mcp.models import MagicLinkUrl
from vividscripts_mcp.oauth.context import require_user_claims


def make_mint_magic_link_tool(
    backend: BackendProtocol,
) -> Callable[[str, str, int], MagicLinkUrl]:
    """Build the ``mint_magic_link`` tool bound to ``backend``."""

    def mint_magic_link(
        project_id: str, view: str = "editor", ttl_seconds: int = 300
    ) -> MagicLinkUrl:
        """Mint a short-lived sign-in URL for the project.

        ``view`` is ``editor`` (default) or ``video``. The link is
        single-use and expires fast (≤5 min) — present it to the user
        to click promptly, don't store it. Returns ``{url, expires_at}``.
        """
        user_id = require_user_claims().sub
        url, expires_at = backend.mint_magic_link(
            user_id=user_id,
            project_id=project_id,
            view=view,
            ttl_seconds=ttl_seconds,
        )
        return MagicLinkUrl(url=url, expires_at=expires_at)

    return mint_magic_link


def make_get_video_download_url_tool(
    backend: BackendProtocol,
) -> Callable[[str], MagicLinkUrl]:
    """Build the ``get_video_download_url`` tool bound to ``backend``."""

    def get_video_download_url(project_id: str) -> MagicLinkUrl:
        """Return a short-lived signed URL to the compiled video.

        Requires the project to have been compiled (``compile_video``)
        — errors otherwise. The URL expires fast (≤5 min); fetch it
        promptly. Returns ``{url, expires_at}``.
        """
        user_id = require_user_claims().sub
        url, expires_at = backend.get_video_download_url(user_id=user_id, project_id=project_id)
        return MagicLinkUrl(url=url, expires_at=expires_at)

    return get_video_download_url


def register_handoff_tools(mcp: FastMCP, backend: BackendProtocol) -> None:
    """Register the Phase-5 URL-handoff tools on the FastMCP server."""
    mcp.tool()(make_mint_magic_link_tool(backend))
    mcp.tool()(make_get_video_download_url_tool(backend))
