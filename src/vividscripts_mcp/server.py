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

from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Mount, Route

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
from vividscripts_mcp.oauth.callback import make_callback_handler
from vividscripts_mcp.oauth.codes import (
    AuthCodeStore,
    AuthRequestStateStore,
    MockAuthCodeStore,
    MockAuthRequestStateStore,
)
from vividscripts_mcp.oauth.cognito import CognitoConfig
from vividscripts_mcp.oauth.dcr import make_register_handler
from vividscripts_mcp.oauth.metadata import (
    AS_METADATA_PATH,
    PRM_PATH,
    BearerEnforcementMiddleware,
    make_as_metadata_handler,
    make_prm_handler,
)
from vividscripts_mcp.oauth.mock_idp import LOGIN_PATH as MOCK_IDP_LOGIN_PATH
from vividscripts_mcp.oauth.mock_idp import make_login_handler
from vividscripts_mcp.oauth.ratelimit import GlobalRateLimiter
from vividscripts_mcp.oauth.session import MockSessionStore, SessionStore
from vividscripts_mcp.oauth.store import ClientStore, MockClientStore
from vividscripts_mcp.oauth.token import make_token_handler
from vividscripts_mcp.oauth.tokens import MockRefreshTokenStore, RefreshTokenStore
from vividscripts_mcp.tools.media import register_media_tools
from vividscripts_mcp.tools.projects import (
    make_create_project_tool,
    make_get_project_tool,
    make_list_projects_tool,
)
from vividscripts_mcp.tools.prompts import register_prompts
from vividscripts_mcp.tools.state import register_state_tools

SERVER_NAME = "vividscripts-mcp"


def _broker_transport_security(cognito: CognitoConfig) -> TransportSecuritySettings:
    """DNS-rebinding allow-list for the real deployment host.

    FastMCP auto-enables DNS-rebinding protection with a localhost-only
    allow-list (its default ``host`` is 127.0.0.1), so in production a
    request with ``Host: vividscripts.ai`` is rejected with HTTP 421
    ("Invalid Host header"). Behind CloudFront → ALB the container sees
    the bare public host (no port), and the SDK's ``:*`` wildcard only
    matches host:port — so the exact bare host must be allow-listed.
    Localhost patterns are kept for the in-cluster / loopback paths.
    """
    host = urlparse(cognito.public_base_url).hostname or ""
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[host, f"{host}:*", "127.0.0.1:*", "localhost:*", "[::1]:*"],
        allowed_origins=[
            cognito.public_base_url,
            "http://127.0.0.1:*",
            "http://localhost:*",
            "http://[::1]:*",
        ],
    )


def create_mcp_server(
    backend: BackendProtocol,
    cognito: CognitoConfig | None = None,
) -> FastMCP:
    """Construct a FastMCP server with the Phase 1 tool surface.

    ``backend`` is injected so the project-management tools (KAN-53) can
    dispatch user-scoped storage operations. The workflow-step stub
    doesn't use it yet — that wiring lands in KAN-30 (Phase 2).

    In broker mode (``cognito`` set) the FastMCP transport's
    DNS-rebinding allow-list is configured for the real deployment host;
    offline keeps FastMCP's localhost auto-protection (tests/dev run on
    127.0.0.1).
    """
    if cognito is not None:
        mcp = FastMCP(
            SERVER_NAME,
            transport_security=_broker_transport_security(cognito),
        )
    else:
        mcp = FastMCP(SERVER_NAME)

    # KAN-53 project tools — user-scoped, Bearer-authenticated.
    mcp.tool()(make_create_project_tool(backend))
    mcp.tool()(make_list_projects_tool(backend))
    mcp.tool()(make_get_project_tool(backend))

    # KAN-58 — 20 MCP Prompts + the backend-served list_workflow_steps
    # (replaces Phase 1's empty-list stub).
    register_prompts(mcp, backend)

    # KAN-59 — save_step_result + get_workflow_state + custom overrides.
    register_state_tools(mcp, backend)

    # KAN-69+ — async media-generation tools (generate_audio, check_job, …).
    register_media_tools(mcp, backend)

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
    cognito: CognitoConfig | None = None,
    dcr_rate_limiter: GlobalRateLimiter | None = None,
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
    for the offline dev server.

    Passing ``cognito`` flips the app into **broker mode** (KAN-85):
    ``/oauth/authorize`` delegates to Cognito Hosted UI, ``/oauth/callback``
    is mounted, ``/oauth/token`` passes Cognito tokens through, the
    Bearer validator checks Cognito access tokens, the PRM/AS-metadata
    documents advertise the real deployment, and the offline mock IdP is
    **not** mounted. With ``cognito`` unset everything stays offline
    (mock IdP + self-mint) so the unit suite runs without a network.
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

    if cognito is not None:

        def _validate(token: str) -> object | None:
            return validate_bearer_token(
                token,
                resolved_jwks_provider,
                issuer=cognito.issuer,
                audience=None,  # Cognito access tokens have no ``aud``.
                expected_client_id=cognito.client_id,
            )

        prm_handler = make_prm_handler(
            resource=f"{cognito.public_base_url}/mcp",
            authorization_servers=[cognito.public_base_url],
        )
    else:

        def _validate(token: str) -> object | None:
            return validate_bearer_token(token, resolved_jwks_provider)

        prm_handler = make_prm_handler()

    routes: list[BaseRoute] = [
        Route("/health", endpoint=health, methods=["GET"]),
        Route(PRM_PATH, endpoint=prm_handler, methods=["GET"]),
        Route(JWKS_PATH, endpoint=jwks_endpoint, methods=["GET"]),
        Route(
            "/oauth/register",
            endpoint=make_register_handler(
                resolved_client_store,
                resolved_session_store,
                # Broker mode: open DCR per RFC 7591 — Cognito Hosted UI
                # is the real auth gate (KAN-85). Offline keeps the
                # session gate (KAN-46).
                session_gated=cognito is None,
                # Global (not per-IP) flood ceiling — KAN-83. Sound
                # per-IP limiting is the edge WAF's job.
                rate_limiter=(
                    dcr_rate_limiter if dcr_rate_limiter is not None else GlobalRateLimiter()
                ),
            ),
            methods=["POST"],
        ),
        Route(
            "/oauth/authorize",
            endpoint=make_authorize_handler(
                resolved_client_store, resolved_request_state_store, cognito
            ),
            methods=["GET"],
        ),
        Route(
            "/oauth/token",
            endpoint=make_token_handler(
                resolved_client_store,
                resolved_code_store,
                resolved_refresh_token_store,
                cognito,
            ),
            methods=["POST"],
        ),
    ]

    if cognito is not None:
        # Broker mode: the package is the AS facade Claude Code
        # discovers (RFC 8414) and Cognito redirects back to.
        routes.append(
            Route(
                AS_METADATA_PATH,
                endpoint=make_as_metadata_handler(cognito.public_base_url),
                methods=["GET"],
            )
        )
        routes.append(
            Route(
                "/oauth/callback",
                endpoint=make_callback_handler(
                    resolved_request_state_store,
                    resolved_code_store,
                    cognito,
                ),
                methods=["GET"],
            )
        )
    else:
        # Offline mode only: the mock IdP must never be mounted in a
        # production (broker) build.
        routes.append(
            Route(
                MOCK_IDP_LOGIN_PATH,
                endpoint=make_login_handler(
                    resolved_session_store,
                    resolved_request_state_store,
                    resolved_code_store,
                ),
                methods=["GET", "POST"],
            )
        )

    mcp = create_mcp_server(resolved_backend, cognito)
    inner = mcp.streamable_http_app()
    routes.append(Mount("/", app=inner))
    return Starlette(
        routes=routes,
        middleware=[Middleware(BearerEnforcementMiddleware, validator=_validate)],
        lifespan=inner.router.lifespan_context,
    )
