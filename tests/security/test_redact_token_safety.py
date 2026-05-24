"""Audit finding #12 — ``redact_token`` log-injection safety.

``redact_token`` accepted a raw decoded-claim dict and emitted
``jti:<jti>`` from it, even on decode-but-reject paths. Two problems:

1. **Log injection.** A ``jti`` carrying CRLF or other control characters
   would split a JSON-line audit record into multiple lines, letting an
   attacker forge log entries downstream of the audit boundary.
2. **Log-correlation forging.** The ``jti:`` prefix is the trustworthy
   correlation handle. Emitting it from *unverified* claims means a
   forged token can sit next to a real token in the log with the same
   ``jti:`` value.

Fix shape:

- Accept ``UserClaims`` only (not raw dicts) for the ``jti``-emit path.
- Sanitize the ``jti`` against ``^[A-Za-z0-9_-]{1,64}$``; if it fails,
  fall back to the SHA-256 prefix unconditionally.
"""

from __future__ import annotations

import pytest

from vividscripts_mcp.oauth.bearer import UserClaims, redact_token


def _user_claims(jti: str) -> UserClaims:
    return UserClaims(
        sub="user-alpha",
        client_id="c",
        scope=None,
        jti=jti,
        exp=9999999999,
        iat=1,
    )


def test_raw_dict_claims_do_not_emit_jti() -> None:
    """A raw decoded-claim dict (the unverified path) must never reach
    the ``jti:`` branch — even if the dict happens to look well-formed."""
    redacted = redact_token("any-token", claims={"jti": "abc-xyz"})  # type: ignore[arg-type]
    assert redacted.startswith("sha256:")
    assert "abc-xyz" not in redacted


def test_validated_user_claims_emit_jti() -> None:
    """``UserClaims`` is the validated-flow signal — its ``jti`` is fine
    to surface (after sanitization)."""
    redacted = redact_token("any-token", claims=_user_claims("abc-xyz"))
    assert redacted == "jti:abc-xyz"


@pytest.mark.parametrize(
    "bad_jti",
    [
        "with\r\ninjection",
        "with\nnewline",
        "with space",
        "with/slash",
        "with?query",
        "x" * 65,  # over the 64-char cap
        "",  # under the 1-char floor
    ],
)
def test_unsafe_jti_falls_back_to_digest(bad_jti: str) -> None:
    redacted = redact_token("any-token", claims=_user_claims(bad_jti))
    assert redacted.startswith("sha256:")
    # The ``not in`` check is only meaningful for non-empty strings;
    # every string contains the empty string. The ``startswith("sha256:")``
    # check above is the load-bearing assertion for that case.
    if bad_jti:
        assert bad_jti not in redacted


def test_redact_token_never_includes_raw_token() -> None:
    raw = "very-secret-bearer-token-value"
    assert raw not in redact_token(raw, claims=_user_claims("safe-jti"))
    assert raw not in redact_token(raw, claims=None)
