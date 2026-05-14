"""Client registration storage for OAuth Dynamic Client Registration.

The MCP server reads from a ``ClientStore`` to validate authorization
requests (KAN-50 will check ``redirect_uri`` against the registered
client's URIs) and to issue tokens (KAN-51 needs the client metadata).

Phase 1 ships ``MockClientStore`` — thread-safe in-memory. Phase 3
(KAN-65) replaces it with an AWS Secrets Manager-backed implementation
storing a single JSON document at ``vividscripts/mcp-clients`` (per the
KAN-35 decision). The Protocol contract stays identical so the swap is
local to one module.
"""

from __future__ import annotations

import threading
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict


class RegisteredClient(BaseModel):
    """A single Dynamic Client Registration record.

    ``owner_user_id`` is the session user who performed the registration
    (per KAN-46 mitigation: DCR requires a prior session). Phase 3's
    real backing will derive this from the Cognito ``sub`` claim.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    client_id: str
    issued_at: int  # Unix timestamp, seconds
    owner_user_id: str
    redirect_uris: tuple[str, ...]
    token_endpoint_auth_method: str
    grant_types: tuple[str, ...]
    response_types: tuple[str, ...]
    client_name: str | None = None


@runtime_checkable
class ClientStore(Protocol):
    """Persistence contract for registered OAuth clients."""

    def add(self, client: RegisteredClient) -> None: ...

    def get(self, client_id: str) -> RegisteredClient | None: ...

    def all(self) -> list[RegisteredClient]: ...


class MockClientStore:
    """Thread-safe in-memory ClientStore.

    Used in tests and the Phase 1 dev server. Holds clients in a dict
    keyed by ``client_id``; not persisted across process restarts.
    """

    def __init__(self) -> None:
        self._clients: dict[str, RegisteredClient] = {}
        self._lock = threading.Lock()

    def add(self, client: RegisteredClient) -> None:
        with self._lock:
            self._clients[client.client_id] = client

    def get(self, client_id: str) -> RegisteredClient | None:
        with self._lock:
            return self._clients.get(client_id)

    def all(self) -> list[RegisteredClient]:
        with self._lock:
            return list(self._clients.values())
