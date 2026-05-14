"""Tests for the MCP server scaffolding (KAN-47)."""

from __future__ import annotations

from starlette.testclient import TestClient

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


async def test_list_workflow_steps_tool_is_registered() -> None:
    """The list_workflow_steps tool is exposed via FastMCP's tool catalog."""
    mcp = create_mcp_server()
    tools = await mcp.list_tools()
    names = {tool.name for tool in tools}
    assert "list_workflow_steps" in names


async def test_list_workflow_steps_returns_empty_in_phase_1() -> None:
    """Phase 1's stub returns an empty list. KAN-30 will replace this."""
    mcp = create_mcp_server()
    result = await mcp.call_tool("list_workflow_steps", {})
    # FastMCP returns (content_blocks, structured_payload). The structured
    # payload is what tool callers consume; for a list return type it wraps
    # the list under the "result" key.
    _, structured = result
    assert structured == {"result": []}
