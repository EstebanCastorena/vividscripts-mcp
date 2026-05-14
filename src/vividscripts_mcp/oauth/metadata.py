"""OAuth 2.0 Protected Resource Metadata endpoint (RFC 9728, KAN-48).

RFC 9728 lets MCP clients (Claude Code) auto-discover the authorization
servers they should authenticate against. The flow:

1. Client hits ``/mcp`` without a Bearer token.
2. Server returns ``401`` with ``WWW-Authenticate: Bearer
   resource_metadata="https://<host>/.well-known/oauth-protected-resource"``.
3. Client fetches that URL → gets back the JSON document this module serves.
4. Client uses the ``authorization_servers`` field to discover OAuth endpoints
   (DCR, authorize, token) and complete the auth dance.

Phase 1 ships placeholder issuer + AS URLs. Production wiring (Cognito issuer,
real deployment host) lands in Phase 3.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

#: Path the PRM document is served from. Per RFC 9728 § 3.1 the well-known
#: suffix is ``oauth-protected-resource``.
PRM_PATH = "/.well-known/oauth-protected-resource"

#: Canonical identifier of the protected resource (the MCP endpoint). Phase 1
#: placeholder; production binds this to the real deployment host.
_RESOURCE = "https://app.vividscripts.com/mcp"

#: Issuer identifiers of the OAuth authorization servers that mint tokens for
#: this resource. Phase 1 placeholder; production lists the Cognito issuer.
_AUTHORIZATION_SERVERS: tuple[str, ...] = ("https://app.vividscripts.com",)

#: GitHub URL of the human-readable auth docs (KAN-55 writes the page).
_RESOURCE_DOCUMENTATION = (
    "https://github.com/EstebanCastorena/vividscripts-mcp/blob/main/docs/auth.md"
)


class ProtectedResourceMetadata(BaseModel):
    """RFC 9728 Protected Resource Metadata document.

    Only the fields the ticket called out are exposed; extra keys are
    forbidden so schema drift surfaces immediately in tests.
    """

    model_config = ConfigDict(extra="forbid")

    resource: str
    authorization_servers: list[str]
    bearer_methods_supported: list[str]
    resource_documentation: str
    scopes_supported: list[str]
    resource_signing_alg_values_supported: list[str]


def build_prm_payload() -> ProtectedResourceMetadata:
    """Return the Phase 1 PRM document with placeholder issuer + AS URLs."""
    return ProtectedResourceMetadata(
        resource=_RESOURCE,
        authorization_servers=list(_AUTHORIZATION_SERVERS),
        bearer_methods_supported=["header"],
        resource_documentation=_RESOURCE_DOCUMENTATION,
        scopes_supported=["openid", "profile", "email"],
        resource_signing_alg_values_supported=["RS256"],
    )


async def protected_resource_metadata(_request: Request) -> JSONResponse:
    """Serve the PRM document at ``GET /.well-known/oauth-protected-resource``."""
    return JSONResponse(build_prm_payload().model_dump())


def _is_mcp_path(path: str) -> bool:
    """The Streamable HTTP transport mounts at ``/mcp`` (with optional subpaths)."""
    return path == "/mcp" or path.startswith("/mcp/")


def _has_bearer(headers: Headers) -> bool:
    auth = headers.get("authorization", "")
    return auth.lower().startswith("bearer ")


def _extract_bearer(headers: Headers) -> str | None:
    auth = headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    token = auth[len("bearer ") :].strip()
    return token or None


def _metadata_url(scope: Scope) -> str:
    """Build the absolute URL of the PRM endpoint from the ASGI scope.

    Using the request's own host means a real client behind any deployment
    URL (or a TestClient pointing at ``http://testserver``) gets back a
    self-consistent ``resource_metadata`` URL it can actually fetch.
    """
    headers = Headers(scope=scope)
    host = headers.get("host")
    if host is None:
        server = scope.get("server") or ("localhost", 80)
        host = f"{server[0]}:{server[1]}"
    scheme = scope.get("scheme", "http")
    return f"{scheme}://{host}{PRM_PATH}"


#: Type alias for the Bearer validator callable. Returns user claims on
#: success, ``None`` on any rejection. KAN-52 wires this in.
BearerValidator = Callable[[str], Any | None]


class BearerEnforcementMiddleware:
    """Reject unauthenticated or invalid ``/mcp`` requests with 401 + WWW-Authenticate.

    The middleware enforces two layers on /mcp:

    1. **Presence.** Any request without ``Authorization: Bearer <...>``
       earns a 401 whose ``WWW-Authenticate`` header points clients at
       the PRM document (RFC 6750 § 3 + RFC 9728 § 5.1).
    2. **Validity** (when a ``validator`` is configured — KAN-52). The
       Bearer token is cryptographically verified (signature, audience,
       issuer, ``token_use``, expiry). On rejection, the response is a
       401 with ``error="invalid_token"`` in the ``WWW-Authenticate``
       header per RFC 6750 § 3.1.

    Validated user claims are stashed on the ASGI scope under
    ``scope["state"]["bearer_claims"]`` so downstream tool handlers can
    read the authenticated user without re-validating.
    """

    def __init__(
        self,
        app: ASGIApp,
        validator: BearerValidator | None = None,
    ) -> None:
        self.app = app
        self.validator = validator

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not _is_mcp_path(path):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        token = _extract_bearer(headers)
        if token is None:
            await self._unauthenticated(scope, receive, send)
            return

        if self.validator is not None:
            claims = self.validator(token)
            if claims is None:
                await self._unauthenticated(scope, receive, send, error="invalid_token")
                return
            # Stash claims for downstream tools. We mutate the state dict
            # rather than the scope's other keys to stay within Starlette's
            # contract for middleware-to-handler state passing.
            state = scope.setdefault("state", {})
            if isinstance(state, dict):
                state["bearer_claims"] = claims
            # Bind the contextvar so MCP tool handlers can read the user
            # without plumbing the ASGI scope through FastMCP internals.
            # Late import: oauth.context depends on oauth.bearer.UserClaims;
            # a top-level import here would force every metadata.py consumer
            # to drag in the JWT machinery.
            from vividscripts_mcp.oauth.context import set_user_claims

            set_user_claims(claims)

        await self.app(scope, receive, send)

    async def _unauthenticated(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        error: str | None = None,
    ) -> None:
        challenge = f'Bearer resource_metadata="{_metadata_url(scope)}"'
        if error is not None:
            challenge += f', error="{error}"'
        response = Response(
            status_code=401,
            headers={"WWW-Authenticate": challenge},
        )
        await response(scope, receive, send)
