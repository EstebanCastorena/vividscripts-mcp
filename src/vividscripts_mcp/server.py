"""MCP Streamable HTTP server entrypoint (KAN-47).

The server is a Starlette ASGI app composed of two pieces:

1. Application-level routes — currently just ``/health``. The OAuth surface
   (KAN-48 protected-resource metadata, KAN-49 dynamic client registration,
   KAN-50 authorize, KAN-51 token) attaches additional routes here.
2. The MCP server's Streamable HTTP transport, mounted at the root so the
   default ``/mcp`` path resolves correctly for clients.

Phase 1's tool surface is intentionally minimal — ``list_workflow_steps``
ships as an empty stub to exercise the wire protocol. Backend-dispatching
tools land starting with KAN-53 (project tools) and KAN-30 (Phase 2 prompts).
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from vividscripts_mcp.adapters.base import BackendProtocol
from vividscripts_mcp.adapters.mock import MockBackend
from vividscripts_mcp.oauth.authorize import make_authorize_handler
from vividscripts_mcp.oauth.bearer import (
    JWKS_PATH,
    InProcessJWKSProvider,
    JWKSProvider,
    jwks_endpoint,
    validate_bearer_token,
)
from vividscripts_mcp.oauth.codes import (
    AuthCodeStore,
    AuthRequestStateStore,
    MockAuthCodeStore,
    MockAuthRequestStateStore,
)
from vividscripts_mcp.oauth.dcr import make_register_handler
from vividscripts_mcp.oauth.metadata import (
    PRM_PATH,
    BearerEnforcementMiddleware,
    protected_resource_metadata,
)
from vividscripts_mcp.oauth.mock_idp import LOGIN_PATH as MOCK_IDP_LOGIN_PATH
from vividscripts_mcp.oauth.mock_idp import make_login_handler
from vividscripts_mcp.oauth.session import MockSessionStore, SessionStore
from vividscripts_mcp.oauth.store import ClientStore, MockClientStore
from vividscripts_mcp.oauth.token import make_token_handler
from vividscripts_mcp.oauth.tokens import MockRefreshTokenStore, RefreshTokenStore
from vividscripts_mcp.tools.projects import (
    make_create_project_tool,
    make_get_project_tool,
    make_list_projects_tool,
)
from vividscripts_mcp.tools.prompts import register_prompts

SERVER_NAME = "vividscripts-mcp"


def create_mcp_server(backend: BackendProtocol) -> FastMCP:
    """Construct a FastMCP server with the Phase 1 tool surface.

    ``backend`` is injected so the project-management tools (KAN-53) can
    dispatch user-scoped storage operations. The workflow-step stub
    doesn't use it yet — that wiring lands in KAN-30 (Phase 2).
    """
    mcp = FastMCP(SERVER_NAME)

    # KAN-53 project tools — user-scoped, Bearer-authenticated.
    mcp.tool()(make_create_project_tool(backend))
    mcp.tool()(make_list_projects_tool(backend))
    mcp.tool()(make_get_project_tool(backend))

    # KAN-58 — 20 MCP Prompts + the backend-served list_workflow_steps
    # (replaces Phase 1's empty-list stub).
    register_prompts(mcp, backend)

    return mcp


async def health(_request: Request) -> JSONResponse:
    """Liveness probe. No auth, no MCP — confirms the process is up."""
    return JSONResponse({"status": "ok"})


def build_app(
    *,
    backend: BackendProtocol | None = None,
    client_store: ClientStore | None = None,
    session_store: SessionStore | None = None,
    request_state_store: AuthRequestStateStore | None = None,
    code_store: AuthCodeStore | None = None,
    refresh_token_store: RefreshTokenStore | None = None,
    jwks_provider: JWKSProvider | None = None,
) -> Starlette:
    """Assemble the ASGI app: Starlette host + mounted FastMCP streamable HTTP.

    Route order matters: ``/health`` and the OAuth surface (PRM document,
    DCR, authorize, mock IdP) are matched before the catch-all MCP Mount.
    The :class:`BearerEnforcementMiddleware` short-circuits naked ``/mcp``
    requests with a 401 + ``WWW-Authenticate`` header so Claude Code can
    discover the PRM endpoint and bootstrap OAuth.

    The MCP transport carries its own lifespan handler (initializes the
    session manager); we propagate it to the outer Starlette app so the
    transport starts and stops cleanly with the host process.

    All four stores are injectable so tests can pre-populate them and
    inspect persisted state. They default to in-memory mocks — appropriate
    for the Phase 1 dev server; Phase 3 swaps the mocks for production
    backings (Cognito sessions, Secrets Manager clients).
    """
    resolved_client_store: ClientStore = (
        client_store if client_store is not None else MockClientStore()
    )
    resolved_session_store: SessionStore = (
        session_store if session_store is not None else MockSessionStore()
    )
    resolved_request_state_store: AuthRequestStateStore = (
        request_state_store if request_state_store is not None else MockAuthRequestStateStore()
    )
    resolved_code_store: AuthCodeStore = (
        code_store if code_store is not None else MockAuthCodeStore()
    )
    resolved_refresh_token_store: RefreshTokenStore = (
        refresh_token_store if refresh_token_store is not None else MockRefreshTokenStore()
    )
    resolved_jwks_provider: JWKSProvider = (
        jwks_provider if jwks_provider is not None else InProcessJWKSProvider()
    )
    resolved_backend: BackendProtocol = backend if backend is not None else MockBackend()

    def _validate(token: str) -> object | None:
        return validate_bearer_token(token, resolved_jwks_provider)

    mcp = create_mcp_server(resolved_backend)
    inner = mcp.streamable_http_app()
    return Starlette(
        routes=[
            Route("/health", endpoint=health, methods=["GET"]),
            Route(PRM_PATH, endpoint=protected_resource_metadata, methods=["GET"]),
            Route(JWKS_PATH, endpoint=jwks_endpoint, methods=["GET"]),
            Route(
                "/oauth/register",
                endpoint=make_register_handler(resolved_client_store, resolved_session_store),
                methods=["POST"],
            ),
            Route(
                "/oauth/authorize",
                endpoint=make_authorize_handler(
                    resolved_client_store, resolved_request_state_store
                ),
                methods=["GET"],
            ),
            Route(
                "/oauth/token",
                endpoint=make_token_handler(
                    resolved_client_store,
                    resolved_code_store,
                    resolved_refresh_token_store,
                ),
                methods=["POST"],
            ),
            Route(
                MOCK_IDP_LOGIN_PATH,
                endpoint=make_login_handler(
                    resolved_session_store,
                    resolved_request_state_store,
                    resolved_code_store,
                ),
                methods=["GET", "POST"],
            ),
            Mount("/", app=inner),
        ],
        middleware=[Middleware(BearerEnforcementMiddleware, validator=_validate)],
        lifespan=inner.router.lifespan_context,
    )
