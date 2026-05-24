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

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ConfigDict
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

#: Path the PRM document is served from. Per RFC 9728 § 3.1 the well-known
#: suffix is ``oauth-protected-resource``.
PRM_PATH = "/.well-known/oauth-protected-resource"

#: Path the RFC 8414 Authorization Server Metadata document is served
#: from. In the broker (KAN-85) the package *is* the authorization
#: server Claude Code registers/authorizes against (it brokers to
#: Cognito underneath), so it must publish this for client discovery.
AS_METADATA_PATH = "/.well-known/oauth-authorization-server"

#: Canonical identifier of the protected resource (the MCP endpoint).
#: Offline-mode placeholder; the broker passes the real deployment URL.
_RESOURCE = "https://app.vividscripts.com/mcp"

#: Issuer identifiers of the OAuth authorization servers that mint tokens
#: for this resource. Offline-mode placeholder; the broker passes the
#: package's own facade base URL (it brokers to Cognito).
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


def build_prm_payload(
    *,
    resource: str = _RESOURCE,
    authorization_servers: list[str] | None = None,
) -> ProtectedResourceMetadata:
    """Build the RFC 9728 PRM document.

    Defaults are the offline-mode placeholders; the broker
    (``server.build_app`` with a ``CognitoConfig``) passes the real
    deployment ``resource`` and the package's own facade base URL as the
    authorization server.
    """
    return ProtectedResourceMetadata(
        resource=resource,
        authorization_servers=(
            authorization_servers
            if authorization_servers is not None
            else list(_AUTHORIZATION_SERVERS)
        ),
        bearer_methods_supported=["header"],
        resource_documentation=_RESOURCE_DOCUMENTATION,
        scopes_supported=["openid", "profile", "email"],
        resource_signing_alg_values_supported=["RS256"],
    )


def make_prm_handler(
    *,
    resource: str = _RESOURCE,
    authorization_servers: list[str] | None = None,
) -> Callable[[Request], Awaitable[JSONResponse]]:
    """Build the ``GET /.well-known/oauth-protected-resource`` handler."""

    async def handler(_request: Request) -> JSONResponse:
        return JSONResponse(
            build_prm_payload(
                resource=resource,
                authorization_servers=authorization_servers,
            ).model_dump()
        )

    return handler


async def protected_resource_metadata(_request: Request) -> JSONResponse:
    """Offline-default PRM handler (placeholder values)."""
    return JSONResponse(build_prm_payload().model_dump())


class AuthorizationServerMetadata(BaseModel):
    """RFC 8414 Authorization Server Metadata for the broker facade.

    Only the fields a DCR + PKCE authorization-code client (Claude Code)
    needs to discover the package's endpoints. ``extra="forbid"`` so
    drift surfaces in tests.
    """

    model_config = ConfigDict(extra="forbid")

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str
    response_types_supported: list[str]
    grant_types_supported: list[str]
    code_challenge_methods_supported: list[str]
    token_endpoint_auth_methods_supported: list[str]
    scopes_supported: list[str]


def build_as_metadata_payload(base_url: str) -> AuthorizationServerMetadata:
    """RFC 8414 document advertising the package's own ``/oauth`` facade.

    ``base_url`` is the package's external base (no trailing slash). The
    facade brokers to Cognito underneath, but to Claude Code it *is* the
    authorization server it registers and authorizes against.
    """
    return AuthorizationServerMetadata(
        issuer=base_url,
        authorization_endpoint=f"{base_url}/oauth/authorize",
        token_endpoint=f"{base_url}/oauth/token",
        registration_endpoint=f"{base_url}/oauth/register",
        response_types_supported=["code"],
        grant_types_supported=["authorization_code", "refresh_token"],
        code_challenge_methods_supported=["S256"],
        token_endpoint_auth_methods_supported=["none"],
        scopes_supported=["openid", "profile", "email"],
    )


def make_as_metadata_handler(
    base_url: str,
) -> Callable[[Request], Awaitable[JSONResponse]]:
    """Build the ``GET /.well-known/oauth-authorization-server`` handler."""

    async def handler(_request: Request) -> JSONResponse:
        return JSONResponse(build_as_metadata_payload(base_url).model_dump())

    return handler


#: Paths that serve unauthenticated traffic. Everything **not** in this
#: allow-list is gated by :class:`BearerEnforcementMiddleware` — see
#: :func:`_is_public_path` for the matching rules (KAN-94, audit
#: finding #2 — invert from default-allow to default-deny).
#:
#: ``/.well-known/*`` covers RFC-9728 protected-resource metadata, the
#: RFC-8414 authorization-server metadata, and the JWKS document.
#: ``/oauth/*`` covers DCR (own session-cookie gate), authorize, token,
#: and the broker-mode callback. ``/_mock_idp/*`` is only mounted in
#: offline mode and is the dev login kickoff. ``/health`` is the
#: liveness probe.
#:
#: Anything else (the inner ``Mount("/")``-served FastMCP transport and
#: any future routes it grows) requires a validated Bearer.
_PUBLIC_EXACT_PATHS: frozenset[str] = frozenset({"/health"})
_PUBLIC_PATH_PREFIXES: tuple[str, ...] = (
    "/.well-known/",
    "/oauth/",
    "/_mock_idp/",
)

#: Sentinel returned by :func:`_normalize_path` when a request contains
#: ``..`` segments. The string deliberately is *not* a routable path so
#: every allow-list check against it falls through to default-deny.
_DENY_SENTINEL = "/__deny__"


def _normalize_path(path: str) -> str:
    """Canonicalize an ASGI path for allow-list matching.

    * Case-fold to lowercase so ``/MCP`` cannot bypass a lowercase gate.
    * Collapse empty (``//``) and single-dot (``.``) segments.
    * Reject ``..`` segments by returning :data:`_DENY_SENTINEL`. We do
      not resolve them: ``/mcp/../health`` would otherwise normalize to
      ``/health`` (an allow-listed path) and yield a traversal bypass.

    The function operates on the post-ASGI-decode path (the ASGI spec
    decodes percent-escapes before the application sees the scope) so
    ``/mcp%2F...`` is already ``/mcp/...`` by the time we see it; the
    same normalization still applies.
    """
    segments: list[str] = []
    for raw in path.split("/"):
        if raw == "" or raw == ".":
            continue
        if raw == "..":
            return _DENY_SENTINEL
        segments.append(raw.lower())
    if not segments:
        return "/"
    return "/" + "/".join(segments)


def _is_public_path(path: str) -> bool:
    """Return ``True`` iff ``path`` is on the unauthenticated allow-list."""
    norm = _normalize_path(path)
    if norm == _DENY_SENTINEL:
        return False
    if norm in _PUBLIC_EXACT_PATHS:
        return True
    return any(norm.startswith(prefix) for prefix in _PUBLIC_PATH_PREFIXES)


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

    Behind a TLS-terminating proxy (the production deployment runs behind
    CloudFront → ALB) the ASGI ``scheme`` is ``http`` even though the
    client spoke ``https``. Honor the proxy's ``X-Forwarded-Proto`` so
    the advertised metadata URL stays ``https`` — a strict OAuth client
    may reject a non-https resource-metadata pointer.
    """
    headers = Headers(scope=scope)
    host = headers.get("host")
    if host is None:
        server = scope.get("server") or ("localhost", 80)
        host = f"{server[0]}:{server[1]}"
    forwarded_proto = headers.get("x-forwarded-proto")
    if forwarded_proto:
        # May be a comma-separated list ("https, http"); the first hop
        # (the original client-facing scheme) is authoritative.
        scheme = forwarded_proto.split(",")[0].strip()
    else:
        scheme = scope.get("scheme", "http")
    return f"{scheme}://{host}{PRM_PATH}"


#: Type alias for the Bearer validator callable. Returns user claims on
#: success, ``None`` on any rejection. KAN-52 wires this in.
BearerValidator = Callable[[str], Any | None]


class BearerEnforcementMiddleware:
    """Default-deny Bearer gate for the MCP server (KAN-94, audit findings #1 + #2).

    Posture is **default-deny**: every HTTP request is gated unless its
    path is on the explicit allow-list (:data:`_PUBLIC_EXACT_PATHS` /
    :data:`_PUBLIC_PATH_PREFIXES`). The previous gate matched only
    ``/mcp`` and ``/mcp/...`` and let everything else through, so
    ``/MCP``, dot-segments, or any future route the inner FastMCP app
    grows (legacy ``/sse``/``/messages`` transports, etc.) bypassed
    auth entirely. Default-deny + path normalization closes that whole
    class of bypass.

    Two enforcement layers run on every gated path:

    1. **Presence.** Any request without ``Authorization: Bearer <...>``
       earns a 401 whose ``WWW-Authenticate`` header points clients at
       the PRM document (RFC 6750 § 3 + RFC 9728 § 5.1).
    2. **Validity** (when a ``validator`` is configured — KAN-52). The
       Bearer token is cryptographically verified (signature, audience,
       issuer, ``token_use``, expiry). On rejection, the response is a
       401 with ``error="invalid_token"`` in the ``WWW-Authenticate``
       header per RFC 6750 § 3.1.

    Validated user claims are bound to the auth-context ``ContextVar``
    so MCP tool handlers can read the authenticated user via
    :func:`vividscripts_mcp.oauth.context.require_user_claims`. The
    ``contextvars.Token`` is captured before the downstream call and
    reset in a ``try/finally`` so the bind is unwound on **every** code
    path — success, early return, or downstream exception. Without the
    reset the bind persists in the caller's context and a stale
    identity leaks into the next code path that reads it (audit
    finding #1).

    Claims are also stashed on the ASGI scope under
    ``scope["state"]["bearer_claims"]`` for any middleware/handler that
    prefers the scope contract over the contextvar.
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
        if _is_public_path(path):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        token = _extract_bearer(headers)
        if token is None:
            await self._unauthenticated(scope, receive, send)
            return

        claims: Any = None
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

        # Bind the contextvar (when we have validated claims) and ensure
        # it is reset on **every** exit path — including downstream
        # exceptions. Late import: oauth.context depends on
        # oauth.bearer.UserClaims; a top-level import here would force
        # every metadata.py consumer to drag in the JWT machinery.
        from vividscripts_mcp.oauth.context import reset_user_claims, set_user_claims

        token_handle = set_user_claims(claims) if claims is not None else None
        try:
            await self.app(scope, receive, send)
        finally:
            if token_handle is not None:
                reset_user_claims(token_handle)

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
