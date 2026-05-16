"""Cognito delegation for the OAuth broker (KAN-85).

Phase 1 shipped a self-contained authorization server with a mock IdP
that minted its own RS256 tokens. KAN-36 settled the production token
strategy as **Cognito-direct pass-through**: the package stays the
RFC 7591 DCR facade Claude Code registers against (Cognito user pools
have no open DCR), but delegates *authentication* to Cognito Hosted UI
and passes Cognito's own tokens through unchanged.

This module holds the Cognito-facing pieces of that broker:

- :class:`CognitoConfig` — the deployment's Cognito coordinates,
  injected by the host (the slide_editor sidecar wires it from env /
  Terraform). Its presence is the flag that flips ``build_app`` from
  offline (mock IdP, self-mint) into broker mode.
- :func:`exchange_code` / :func:`refresh_tokens` — confidential calls
  to Cognito's token endpoint using the app client secret. The
  ``/oauth/callback`` and ``/oauth/token`` handlers use these.

Bearer validation of the Cognito tokens is handled in
:mod:`vividscripts_mcp.oauth.bearer` (Cognito access tokens carry
``client_id`` + ``token_use`` and no ``aud``, matching the slide_editor
``cognito_auth.decode_bearer_token`` contract from KAN-64).
"""

from __future__ import annotations

from typing import Any

import httpx
import jwt
from pydantic import BaseModel, ConfigDict, field_validator

#: Default OAuth scopes requested at the Hosted UI. ``openid`` is
#: required for an OIDC login; ``profile``/``email`` match the Cognito
#: app client's allowed scopes (Terraform ``14-cognito.tf``).
DEFAULT_SCOPES: tuple[str, ...] = ("openid", "profile", "email")

#: Network timeout for the confidential token-endpoint calls. Cognito's
#: token endpoint is fast; a tight timeout keeps a hung IdP from
#: stalling the user's browser redirect.
_TOKEN_HTTP_TIMEOUT = 10.0


def _strip_trailing_slash(value: str) -> str:
    return value.rstrip("/")


class CognitoConfig(BaseModel):
    """Cognito coordinates for the broker. Injected by the host process.

    All URLs are normalized without a trailing slash so the derived
    endpoint properties concatenate cleanly.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: Cognito user-pool issuer, e.g.
    #: ``https://cognito-idp.us-east-1.amazonaws.com/us-east-1_AbC123``.
    #: Also the issuer the Bearer validator checks.
    issuer: str

    #: The Cognito **app client id**. Cognito access tokens carry this as
    #: ``client_id`` (not ``aud``); the Bearer validator enforces it.
    client_id: str

    #: The Cognito app **client secret** — this is a confidential client
    #: so the code/refresh exchanges authenticate with HTTP Basic.
    client_secret: str

    #: Hosted UI origin, e.g. ``https://auth.vividscripts.ai``. The
    #: authorize/token endpoints hang off ``/oauth2/*`` here.
    hosted_ui_domain: str

    #: This package's own externally-reachable base URL, e.g.
    #: ``https://vividscripts.ai``. Used to build the ``/oauth/callback``
    #: redirect_uri Cognito sends the user back to, and the PRM resource.
    public_base_url: str

    #: Scopes requested at the Hosted UI.
    scopes: tuple[str, ...] = DEFAULT_SCOPES

    @field_validator("issuer", "hosted_ui_domain", "public_base_url")
    @classmethod
    def _normalize_url(cls, value: str) -> str:
        return _strip_trailing_slash(value)

    @property
    def callback_url(self) -> str:
        """The ``redirect_uri`` Cognito redirects back to after login."""
        return f"{self.public_base_url}/oauth/callback"

    @property
    def authorize_endpoint(self) -> str:
        return f"{self.hosted_ui_domain}/oauth2/authorize"

    @property
    def token_endpoint(self) -> str:
        return f"{self.hosted_ui_domain}/oauth2/token"


class CognitoTokens(BaseModel):
    """The token set Cognito returns from its token endpoint.

    ``extra="ignore"`` so additional Cognito fields don't break parsing;
    only the fields the broker passes through are modeled.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    access_token: str
    expires_in: int
    token_type: str = "Bearer"
    refresh_token: str | None = None
    id_token: str | None = None


async def exchange_code(
    config: CognitoConfig,
    *,
    code: str,
    redirect_uri: str,
) -> CognitoTokens | None:
    """Exchange a Cognito authorization code for tokens (confidential).

    Returns ``None`` on any HTTP, status, or parse failure — the caller
    translates that into an OAuth error without leaking which step
    failed.
    """
    return await _post_token(
        config,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": config.client_id,
        },
    )


async def refresh_tokens(
    config: CognitoConfig,
    *,
    refresh_token: str,
) -> CognitoTokens | None:
    """Proxy a refresh-token grant to Cognito. ``None`` on any failure.

    Cognito does not rotate the refresh token on use, so the response
    carries no ``refresh_token``; the caller keeps reusing the original.
    """
    return await _post_token(
        config,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": config.client_id,
        },
    )


async def _post_token(
    config: CognitoConfig,
    *,
    data: dict[str, str],
) -> CognitoTokens | None:
    try:
        async with httpx.AsyncClient(timeout=_TOKEN_HTTP_TIMEOUT) as client:
            response = await client.post(
                config.token_endpoint,
                data=data,
                # Confidential client: HTTP Basic per RFC 6749 § 2.3.1.
                auth=(config.client_id, config.client_secret),
                headers={"Accept": "application/json"},
            )
        if response.status_code != 200:
            return None
        return CognitoTokens.model_validate(response.json())
    except (httpx.HTTPError, ValueError):
        return None


def subject_from_token(token: str) -> str | None:
    """Best-effort extract of the Cognito ``sub`` from an access token.

    Decoded **without** signature verification: the token was just
    received directly from Cognito's token endpoint over TLS via a
    confidential (client-secret) exchange, so it is trusted at this
    point. The ``sub`` is used only to bind/audit the one-shot package
    code — the authoritative cryptographic check happens on every
    ``/mcp`` Bearer call (:mod:`vividscripts_mcp.oauth.bearer`).
    """
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            options={"verify_signature": False},
        )
    except jwt.InvalidTokenError:
        return None
    sub = claims.get("sub")
    return sub if isinstance(sub, str) and sub else None
