"""Bearer token validation (RFC 6750) — KAN-52.

Validates ``Authorization: Bearer <jwt>`` headers against the public
key(s) of the configured authorization server. Phase 1 uses an
in-process key provider that returns the same RSA key /oauth/token signs
with; Phase 3 swaps in an HTTP-fetching JWKS provider pointed at Cognito.

Security guarantees, all tested:

- **Explicit algorithm allow-list.** ``algorithms=["RS256"]`` is passed
  to :func:`jwt.decode`; HS256, ``none``, or any other algorithm is
  rejected (Security AC #4 on KAN-29).
- **Audience + issuer claim checks.** Both ``aud`` and ``iss`` must
  match the configured values exactly (Security AC #5).
- **Token-use claim verified.** Tokens whose ``token_use`` is anything
  other than ``"access"`` are rejected — refresh tokens, ID tokens, etc.
  can't be used as Bearer credentials.
- **Token redaction in logs.** :func:`redact_token` returns a stable
  fingerprint (``jti`` from the claims when available, otherwise the
  first 8 hex chars of the token's SHA-256). The raw token is never
  emitted (Security AC #6).
"""

from __future__ import annotations

import hashlib
import re
import time
from typing import Any, Protocol, runtime_checkable

import jwt
from pydantic import BaseModel, ConfigDict, field_validator
from starlette.requests import Request
from starlette.responses import JSONResponse

from vividscripts_mcp.oauth.keys import ALGORITHM, get_signing_key
from vividscripts_mcp.oauth.tokens import DEFAULT_AUDIENCE, DEFAULT_ISSUER

#: Path the JWKS document is served from. Standard well-known suffix.
JWKS_PATH = "/.well-known/jwks.json"

#: KAN-95 finding #3 — standard claims required at the decode layer (not
#: incidentally via :class:`UserClaims`'s required Pydantic fields). A
#: refactor of :class:`UserClaims` therefore cannot silently disable
#: required-claim enforcement.
_REQUIRED_CLAIMS = ("exp", "iat", "iss", "sub", "jti")

#: KAN-95 finding #3 — clock-skew tolerance for ``iat`` only.
#: PyJWT 2.x with ``leeway=0`` raises :class:`jwt.ImmatureSignatureError` for
#: any future-dated ``iat``; 60s absorbs ordinary client/server clock drift.
#: We deliberately do *not* pass this as PyJWT's ``leeway=`` kwarg because
#: that would also extend ``exp`` tolerance — accepting tokens that have
#: been expired for up to 60s. ``iat`` is checked manually post-decode with
#: PyJWT's iat validation disabled (``verify_iat: False``); ``exp`` stays
#: strict.
_IAT_LEEWAY_SECONDS = 60

# KAN-97 #12 — only ``jti`` values matching this pattern are safe to emit
# verbatim into an audit log line. Anything outside the alphabet (CRLF,
# spaces, slashes, quotes, control chars, oversize ids) falls back to
# the SHA-256 prefix so an attacker cannot forge log-line boundaries or
# correlation handles from a rejected token.
_SAFE_JTI_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

#: KAN-98 #21 — known scope values for the protected-resource. ``scope`` is
#: passed verbatim from the access token into :class:`UserClaims`; bounding
#: the alphabet here keeps an upstream-bug-induced unknown scope from
#: silently flowing into any future scope-based authz check.
_ALLOWED_SCOPES: frozenset[str] = frozenset(
    {
        "openid",
        "profile",
        "email",
        # Cognito's app-client surface adds this one in real deployments.
        # Bounding the allow-list to known values is the goal; allowing the
        # one Cognito scope we actually request keeps the broker path live.
        "aws.cognito.signin.user.admin",
    }
)


class UserClaims(BaseModel):
    """The subset of JWT claims the MCP tool layer relies on."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sub: str
    client_id: str
    scope: str | None
    jti: str
    exp: int
    iat: int

    @field_validator("scope")
    @classmethod
    def _validate_scope(cls, value: str | None) -> str | None:
        """KAN-98 #21 — allow-list known OAuth scope values.

        ``scope`` arrives space-delimited (RFC 6749 § 3.3). An empty
        string is treated as ``None`` (Cognito sometimes omits the
        claim, sometimes sends an empty string); any token not in
        :data:`_ALLOWED_SCOPES` raises ``ValueError``, which the
        validator's caller translates into a ``None`` rejection on the
        Bearer path. Informational today (tools only read ``.sub``);
        prevents drift before Phase-3 adds scope-based authz.
        """
        if value is None or value == "":
            return None
        tokens = value.split()
        unknown = [t for t in tokens if t not in _ALLOWED_SCOPES]
        if unknown:
            msg = f"unknown scope values: {unknown!r}"
            raise ValueError(msg)
        return value


@runtime_checkable
class JWKSProvider(Protocol):
    """Resolver for the JWK matching a given ``kid``.

    Phase 1 ships :class:`InProcessJWKSProvider`; Phase 3 will add an
    HTTP-fetching provider with 1-hour caching and auto-refresh on
    ``kid`` miss against the Cognito user pool's JWKS endpoint.
    """

    def get_jwk(self, kid: str) -> dict[str, Any] | None: ...


class InProcessJWKSProvider:
    """Phase 1 JWKS provider — returns the in-process signing key."""

    def get_jwk(self, kid: str) -> dict[str, Any] | None:
        jwk = get_signing_key().public_jwk
        if jwk["kid"] != kid:
            return None
        return jwk


def redact_token(token: str, claims: UserClaims | None = None) -> str:
    """Return a non-reversible fingerprint suitable for logging.

    Prefers the ``jti`` claim, but **only** when ``claims`` is a fully
    validated :class:`UserClaims` instance — passing a raw decoded-claim
    dict on a reject path is no longer enough to flip into the ``jti:``
    branch. The ``jti`` is then sanitized against
    :data:`_SAFE_JTI_PATTERN`; anything that would let an attacker forge
    log-line boundaries or correlation handles falls back to the
    SHA-256 prefix. The raw token is never returned. KAN-97 #12.
    """
    # ``isinstance`` (not just ``is not None``) so a caller that passed
    # a raw decoded-claim dict on a reject path can never reach this
    # branch — defensive against the pre-KAN-97 signature.
    if isinstance(claims, UserClaims) and _SAFE_JTI_PATTERN.fullmatch(claims.jti):
        return f"jti:{claims.jti}"
    # KAN-98 #17 — emit the full SHA-256 digest rather than a 64-bit
    # truncation. 64 bits is plenty for preimage resistance but invites
    # cross-log correlation forgery; the full digest closes that gap and
    # the byte cost is negligible.
    digest = hashlib.sha256(token.encode("ascii")).hexdigest()
    return f"sha256:{digest}"


def validate_bearer_token(
    token: str,
    provider: JWKSProvider,
    *,
    issuer: str = DEFAULT_ISSUER,
    audience: str | None = DEFAULT_AUDIENCE,
    expected_client_id: str | None = None,
) -> UserClaims | None:
    """Validate a Bearer JWT. Returns the claims on success, ``None`` on any failure.

    Two modes, selected by the caller (``server.build_app``):

    - **Offline** (``audience`` set, ``expected_client_id`` unset) — the
      Phase-1 self-minted token carries ``aud``; it's checked by
      ``jwt.decode``. This is the default, so existing callers are
      unaffected.
    - **Cognito broker** (KAN-85: ``audience=None``,
      ``expected_client_id`` set) — Cognito **access** tokens carry no
      ``aud``; audience verification is disabled and the app-client
      identity is enforced manually against the ``client_id`` claim,
      mirroring the slide_editor ``cognito_auth.decode_bearer_token``
      contract (KAN-64).

    In both modes RS256 is pinned (no algorithm fallback), the issuer is
    checked, ``token_use`` must be ``access``, and the ``kid`` must
    resolve via the injected JWKS provider.

    The function deliberately returns ``None`` (rather than raising) so
    the calling middleware can produce a consistent 401 response without
    leaking which specific check failed. The validator logs the redacted
    token fingerprint and the failure category at INFO; never the raw
    token, never the failed claim values.
    """
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError:
        return None

    kid = header.get("kid")
    if not isinstance(kid, str):
        return None

    jwk = provider.get_jwk(kid)
    if jwk is None:
        return None

    try:
        key = jwt.PyJWK(jwk).key
    except jwt.PyJWKError:
        return None

    # KAN-95 finding #4 — bind the resolved JWK to RSA / signature use / the
    # pinned algorithm before trusting its signature. The ``algorithms=[…]``
    # pin alone is insufficient defense once the Phase-3 HTTP JWKS provider
    # can return a multi-key or non-RSA set (key-confusion). ``alg`` and
    # ``use`` are optional per RFC 7517, so absent metadata defaults to the
    # expected value rather than rejecting.
    if jwk.get("kty") != "RSA":
        return None
    if jwk.get("use", "sig") != "sig":
        return None
    if jwk.get("alg", ALGORITHM) != ALGORITHM:
        return None

    # KAN-95 finding #3 — enforce a complete claim policy at the decode
    # layer. ``require`` rejects tokens missing standard claims so expiry
    # / required-claim enforcement no longer rides on :class:`UserClaims`'s
    # required Pydantic fields. ``verify_iat: False`` disables PyJWT's
    # strict iat check (we re-check it below with a small leeway so
    # ``exp`` tolerance is not accidentally extended too).
    options: dict[str, Any] = {
        "require": list(_REQUIRED_CLAIMS),
        "verify_signature": True,
        "verify_iat": False,
    }
    decode_kwargs: dict[str, Any] = {
        "algorithms": [ALGORITHM],  # Explicit — RS256 only, no fallback.
        "issuer": issuer,
        "options": options,
    }
    if audience is not None:
        decode_kwargs["audience"] = audience
    else:
        # Cognito access tokens have no ``aud``; identity is enforced
        # below against ``client_id`` instead.
        options["verify_aud"] = False

    try:
        claims = jwt.decode(token, key=key, **decode_kwargs)
    except jwt.InvalidTokenError:
        return None

    # KAN-95 finding #3 — manual iat skew check (PyJWT's verify_iat is
    # disabled above so ``leeway`` does not also weaken ``exp``).
    try:
        iat_value = int(claims["iat"])
    except (KeyError, TypeError, ValueError):
        return None
    if iat_value > int(time.time()) + _IAT_LEEWAY_SECONDS:
        return None

    if claims.get("token_use") != "access":
        return None

    if expected_client_id is not None and claims.get("client_id") != expected_client_id:
        return None

    try:
        return UserClaims(
            sub=claims["sub"],
            client_id=claims["client_id"],
            scope=claims.get("scope"),
            jti=claims["jti"],
            exp=int(claims["exp"]),
            iat=int(claims["iat"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


async def jwks_endpoint(_request: Request) -> JSONResponse:
    """``GET /.well-known/jwks.json`` — serves the active public JWK set."""
    key = get_signing_key()
    return JSONResponse({"keys": [key.public_jwk]})
