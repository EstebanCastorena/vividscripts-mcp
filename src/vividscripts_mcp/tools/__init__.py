"""MCP tool implementations.

Each module under ``tools/`` exports factory functions that build a
closure binding a :class:`vividscripts_mcp.adapters.base.BackendProtocol`
implementation. ``server.create_mcp_server`` registers the resulting
closures via ``@mcp.tool()``.

The factory pattern hides the backend dependency from the MCP tool's
public schema — FastMCP only sees the tool's input parameters, never
the injected backend.
"""
