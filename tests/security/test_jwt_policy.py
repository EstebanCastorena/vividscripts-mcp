"""Security regression tests for KAN-95 — JWT claim policy + JWK key-confusion binding.

These tests cover the two HIGH-severity findings from the 2026-05-17 audit:

* **Finding #3** — ``jwt.decode`` had no explicit ``options={"require": [...]}``
  policy; expiry/required-claim enforcement was an incidental side effect of
  :class:`UserClaims`'s required Pydantic fields. A future refactor of the
  model would silently disable expiry enforcement. The validator also did
  not tolerate small ``iat`` clock skew (PyJWT 2.13 with default ``leeway=0``
  rejects any future-dated ``iat``), which would reject legitimate clients
  whose clock is a few seconds ahead.
* **Finding #4** — the JWK resolved by the JWKS provider was passed straight
  to :func:`jwt.PyJWK` without binding its ``kty`` / ``alg`` / ``use`` to the
  pinned RS256 / signature use. The algorithm-pin (``RS256``) was the only
  defense against key-confusion, which becomes exploitable the moment the
  Phase-3 HTTP JWKS provider returns a multi-key set or any non-RSA key.

Audit reference: ``Projects/VividScripts/MCP/Security Review/
2026-05-17 Comprehensive Repo Audit.md`` — findings #3 and #4.

Each test below is annotated with whether it is expected to fail on
``origin/main`` (a TDD-failing regression test) or pass on both pre- and
post-fix (a regression *guard* documenting behavior that is already correct
but easy to lose to refactors).
"""

from __future__ import annotations

import base64
import time
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from vividscripts_mcp.oauth import bearer
from vividscripts_mcp.oauth.bearer import (
    InProcessJWKSProvider,
    UserClaims,
    validate_bearer_token,
)
from vividscripts_mcp.oauth.keys import ALGORITHM, KID, get_signing_key, reset_signing_key
from vividscripts_mcp.oauth.tokens import DEFAULT_AUDIENCE, DEFAULT_ISSUER


@pytest.fixture(autouse=True)
def _fresh_key() -> Iterator[None]:
    reset_signing_key()
    yield
    reset_signing_key()


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _base_claims(**overrides: Any) -> dict[str, Any]:
    """A complete, valid claim set; ``overrides`` may drop or replace fields."""
    now = int(time.time())
    base: dict[str, Any] = {
        "iss": DEFAULT_ISSUER,
        "aud": DEFAULT_AUDIENCE,
        "sub": "user-alpha",
        "client_id": "test-client",
        "iat": now,
        "exp": now + 3600,
        "token_use": "access",
        "jti": "tid-123",
    }
    base.update(overrides)
    return base


def _encode_with(claims: dict[str, Any], headers: dict[str, Any] | None = None) -> str:
    """Sign a token with the in-process RSA key (RS256)."""
    key = get_signing_key()
    return jwt.encode(
        claims,
        key.private_pem,
        algorithm=ALGORITHM,
        headers=headers or {"kid": KID},
    )


class _PermissiveUserClaims:
    """Stand-in for :class:`UserClaims` that accepts any kwargs.

    Used to isolate the *decode layer* from :class:`UserClaims`'s required-field
    side effect — the audit's core concern (a future model refactor silently
    disables required-claim enforcement).
    """

    def __init__(self, **kwargs: Any) -> None:
        self._data = kwargs

    def __getattr__(self, item: str) -> Any:
        return self._data.get(item)


# Capture the real ``jwt.decode`` at module import so the wrapper below can
# call it from inside a ``patch.object(bearer.jwt, "decode", ...)`` context
# without recursing into the mock.
_REAL_JWT_DECODE = jwt.decode


def _decode_with_safe_defaults(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Wrap ``jwt.decode`` to inject safe defaults for constructor-required keys.

    The validator builds :class:`UserClaims` via direct ``claims["..."]`` access
    after a successful decode. Those accesses raise :class:`KeyError` on a
    missing key and are caught — that is the *incidental* enforcement path the
    audit warns about. To prove the new decode-layer policy is what is
    actually rejecting the token, we backstop the constructor accesses so they
    can never raise on missing keys.
    """
    payload = _REAL_JWT_DECODE(*args, **kwargs)
    payload.setdefault("sub", "u")
    payload.setdefault("client_id", "c")
    payload.setdefault("jti", "j")
    payload.setdefault("exp", 0)
    payload.setdefault("iat", 0)
    return payload


# ---------------------------------------------------------------------------
# Finding #3 — explicit JWT claim policy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("missing_claim", ["exp", "iat", "sub", "jti"])
def test_decode_rejects_token_missing_required_claim_independent_of_user_claims(
    missing_claim: str,
) -> None:
    """Rejection of missing exp/iat/sub/jti must happen at the decode layer.

    Expected to FAIL on ``origin/main`` (the rejection happens only
    incidentally — via :class:`UserClaims` field requirements / ``KeyError`` in
    the constructor) and PASS once ``jwt.decode`` is called with
    ``options={"require": [...]}``.

    Note: ``iss`` is covered by a separate test below because PyJWT already
    rejects missing ``iss`` when the ``issuer=`` kwarg is supplied, so it
    cannot be made to fail on ``main`` using this isolation pattern.
    """
    claims = _base_claims()
    claims.pop(missing_claim)
    token = _encode_with(claims)

    with (
        patch.object(bearer, "UserClaims", _PermissiveUserClaims),
        patch.object(bearer.jwt, "decode", side_effect=_decode_with_safe_defaults),
    ):
        result = validate_bearer_token(token, InProcessJWKSProvider())

    assert result is None, (
        f"validator must reject token missing {missing_claim!r} at the decode "
        "layer (options.require), not via UserClaims field requirements"
    )


def test_decode_rejects_token_missing_iss_regression_guard() -> None:
    """Already enforced by PyJWT's ``issuer=`` kwarg path; documents that.

    Passes on both pre- and post-fix code: PyJWT's :class:`MissingRequiredClaimError`
    on missing ``iss`` (when ``issuer=`` is set on ``jwt.decode``) makes this
    rejection independent of the new ``options.require`` policy. Kept as a
    regression guard against future PyJWT behavior changes.
    """
    claims = _base_claims()
    claims.pop("iss")
    token = _encode_with(claims)
    assert validate_bearer_token(token, InProcessJWKSProvider()) is None


def test_decode_accepts_iat_within_small_skew() -> None:
    """An ``iat`` a few seconds in the future (legitimate clock drift) must validate.

    Expected to FAIL on ``origin/main`` — PyJWT 2.13 with the default
    ``leeway=0`` raises :class:`ImmatureSignatureError` for any future-dated
    ``iat``. The fix adds a small leeway so legitimate clients with a fast
    clock are not rejected.
    """
    now = int(time.time())
    token = _encode_with(_base_claims(iat=now + 5, exp=now + 3600))
    result = validate_bearer_token(token, InProcessJWKSProvider())
    assert result is not None
    assert isinstance(result, UserClaims)


def test_decode_rejects_future_iat_beyond_skew() -> None:
    """A token with ``iat`` one hour in the future must still be rejected.

    Regression guard: PyJWT already rejects this with ``leeway=0``; after the
    fix introduces a small leeway (≤ 60s) the rejection still holds for
    egregiously future ``iat``. Documents the audit AC: ``reject if iat > now
    + 60s`` cannot be evaded by setting ``iat`` to ``now + 1h``.
    """
    now = int(time.time())
    token = _encode_with(_base_claims(iat=now + 3600, exp=now + 7200))
    assert validate_bearer_token(token, InProcessJWKSProvider()) is None


# ---------------------------------------------------------------------------
# Finding #4 — JWK kty/alg/use binding
# ---------------------------------------------------------------------------


class _StaticJWKSProvider:
    """JWKS provider that returns one fixed JWK for the matching ``kid``."""

    def __init__(self, jwk: dict[str, Any]) -> None:
        self._jwk = jwk

    def get_jwk(self, kid: str) -> dict[str, Any] | None:
        if self._jwk.get("kid") != kid:
            return None
        return dict(self._jwk)


def _real_rsa_public_jwk() -> dict[str, Any]:
    """A mutable copy of the in-process signing key's public JWK."""
    return dict(get_signing_key().public_jwk)


def _real_ec_p256_jwk(kid: str) -> dict[str, Any]:
    """A valid P-256 EC public JWK (so :class:`jwt.PyJWK` accepts it)."""
    private = ec.generate_private_key(ec.SECP256R1())
    nums = private.public_key().public_numbers()
    return {
        "kty": "EC",
        "kid": kid,
        "alg": "ES256",
        "use": "sig",
        "crv": "P-256",
        "x": _b64url(nums.x.to_bytes(32, "big")),
        "y": _b64url(nums.y.to_bytes(32, "big")),
    }


def test_jwk_with_oct_kty_rejected_before_signature_check() -> None:
    """A matching-``kid`` JWK with ``kty=oct`` (HMAC) must be rejected by the binding.

    Expected to FAIL on ``origin/main`` and PASS post-fix.

    The algorithm pin (``RS256``) already blocks signature verification when
    the resolved key is HMAC, but the binding check must fire *before*
    :func:`jwt.decode` so the rejection is independent of PyJWT's natural
    algorithm/key mismatch. We patch :func:`jwt.decode` to a no-op so that
    only the new binding check can produce a ``None`` result.
    """
    oct_jwk = {
        "kty": "oct",
        "kid": KID,
        "alg": "HS256",
        "use": "sig",
        "k": _b64url(b"a" * 32),
    }
    provider = _StaticJWKSProvider(oct_jwk)
    token = _encode_with(_base_claims())

    with patch.object(bearer.jwt, "decode", return_value=_base_claims()):
        assert validate_bearer_token(token, provider) is None


def test_jwk_with_ec_kty_rejected_before_signature_check() -> None:
    """A matching-``kid`` JWK with ``kty=EC`` must be rejected by the binding.

    Expected to FAIL on ``origin/main`` and PASS post-fix. Same reasoning as
    the ``oct`` test — :func:`jwt.decode` is patched so only the binding
    check can reject.
    """
    provider = _StaticJWKSProvider(_real_ec_p256_jwk(KID))
    token = _encode_with(_base_claims())

    with patch.object(bearer.jwt, "decode", return_value=_base_claims()):
        assert validate_bearer_token(token, provider) is None


def test_jwk_with_mismatched_alg_metadata_rejected() -> None:
    """A real RSA JWK whose ``alg`` metadata is ``RS512`` (token is RS256) is rejected.

    Expected to FAIL on ``origin/main`` and PASS post-fix.

    No :func:`jwt.decode` patching needed: the token signature verifies fine
    against the matching RSA key, so without the binding the validator would
    accept the token despite the ``alg`` metadata mismatch.
    """
    jwk = _real_rsa_public_jwk()
    jwk["alg"] = "RS512"
    provider = _StaticJWKSProvider(jwk)
    token = _encode_with(_base_claims())
    assert validate_bearer_token(token, provider) is None


def test_jwk_with_use_enc_rejected() -> None:
    """A real RSA JWK marked ``use=enc`` (encryption-only) must be rejected.

    Expected to FAIL on ``origin/main`` and PASS post-fix.
    """
    jwk = _real_rsa_public_jwk()
    jwk["use"] = "enc"
    provider = _StaticJWKSProvider(jwk)
    token = _encode_with(_base_claims())
    assert validate_bearer_token(token, provider) is None


def test_jwk_without_alg_metadata_still_accepted_with_rs256_pin() -> None:
    """A JWK without an ``alg`` field must still validate.

    Regression guard: JWK ``alg`` metadata is optional per RFC 7517; the
    pinned ``RS256`` algorithm in the validator is the source of truth.
    """
    jwk = _real_rsa_public_jwk()
    jwk.pop("alg", None)
    provider = _StaticJWKSProvider(jwk)
    token = _encode_with(_base_claims())
    result = validate_bearer_token(token, provider)
    assert result is not None
    assert isinstance(result, UserClaims)


def test_jwk_without_use_metadata_still_accepted_as_sig() -> None:
    """A JWK without a ``use`` field must default to ``sig`` and validate.

    Regression guard: ``use`` is also optional per RFC 7517.
    """
    jwk = _real_rsa_public_jwk()
    jwk.pop("use", None)
    provider = _StaticJWKSProvider(jwk)
    token = _encode_with(_base_claims())
    result = validate_bearer_token(token, provider)
    assert result is not None
    assert isinstance(result, UserClaims)


# ---------------------------------------------------------------------------
# Belt-and-braces — the happy path checked in tests/unit/test_oauth_bearer.py
# still works under the new policy.
# ---------------------------------------------------------------------------


def test_valid_token_with_default_provider_still_validates() -> None:
    """The hardening must not break the canonical valid-token path."""
    token = _encode_with(_base_claims(iss=DEFAULT_ISSUER, aud=DEFAULT_AUDIENCE))
    result = validate_bearer_token(token, InProcessJWKSProvider())
    assert result is not None
    assert isinstance(result, UserClaims)
