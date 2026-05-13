"""Pluggable backends for the MCP server.

The MCP tool layer talks to a `BackendProtocol` implementation. This package
ships `MockBackend`, an in-memory implementation used in tests. Production
deployments inject a real backend at server startup (lives in a separate
private repo).
"""

from vividscripts_mcp.adapters.base import BackendProtocol
from vividscripts_mcp.adapters.mock import MockBackend

__all__ = ["BackendProtocol", "MockBackend"]
