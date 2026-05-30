"""Tests for the MCP server scaffolding (KAN-47)."""

from __future__ import annotations

from starlette.testclient import TestClient

from vividscripts_mcp.adapters.mock import MockBackend
from vividscripts_mcp.server import build_app, create_mcp_server


def test_health_returns_ok() -> None:
    """The /health route returns 200 with a JSON status payload."""
    with TestClient(build_app()) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_mcp_endpoint_mounted() -> None:
    """The MCP transport is mounted at /mcp (i.e., requests don't 404).

    Streamable HTTP requires POST with specific headers, so a plain GET will
    fail — but it should fail with something other than a 404, which is what
    proves the mount is wired. The full handshake test lives in KAN-54.

    TestClient is used as a context manager so Starlette runs the inner app's
    lifespan, which is what initializes the MCP session manager's task group.
    """
    with TestClient(build_app()) as client:
        response = client.get("/mcp")
    assert response.status_code != 404


def test_server_runs_stateless() -> None:
    """The transport is stateless in both offline and broker modes (KAN-123).

    Stateless mode is what makes the server survive a transient transport
    drop mid-pipeline: there is no server-side session to evict, so the
    client's next Bearer-authenticated call simply succeeds instead of
    hitting "Session not found" (HTTP 404). A refactor that drops
    ``stateless_http=True`` would silently reintroduce that bug, so pin it
    here as well as at the wire level (``test_stateless_session_resilience``).
    """
    from vividscripts_mcp.oauth.cognito import CognitoConfig

    assert create_mcp_server(MockBackend()).settings.stateless_http is True

    cognito = CognitoConfig(
        issuer="https://cognito-idp.us-east-1.amazonaws.com/us-east-1_test",
        client_id="test-client",
        client_secret="super-secret",
        hosted_ui_domain="https://auth.vividscripts.ai/",
        public_base_url="https://vividscripts.ai/",
    )
    assert create_mcp_server(MockBackend(), cognito).settings.stateless_http is True


async def test_list_workflow_steps_tool_is_registered() -> None:
    """The list_workflow_steps tool is exposed via FastMCP's tool catalog."""
    mcp = create_mcp_server(MockBackend())
    tools = await mcp.list_tools()
    names = {tool.name for tool in tools}
    assert "list_workflow_steps" in names


async def test_list_workflow_steps_serves_backend_catalog() -> None:
    """KAN-58 replaced Phase 1's `return []` stub with the backend catalog.

    (Was test_list_workflow_steps_returns_empty_in_phase_1; the empty-list
    contract is gone — the tool now serves backend.list_workflow_steps().)
    """
    mcp = create_mcp_server(MockBackend())
    _content, structured = await mcp.call_tool("list_workflow_steps", {})
    steps = structured["result"]
    assert isinstance(steps, list)
    assert len(steps) > 0, "stub is gone — the catalog must be non-empty"
