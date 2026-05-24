"""Sec-E / KAN-98 — PKCE verifier compared in constant time (audit finding #14).

The PKCE preimage-resistance of SHA-256 makes a timing attack against the
``code_verifier`` largely theoretical, but the comparison is also the
project's idiom for secret-equal-secret checks. Hardening it on principle
removes a future foot-gun and sets the pattern for any new comparison
under ``oauth/``.

Two complementary assertions:

1. **Source-grep** — the implementation calls ``hmac.compare_digest`` and
   does not fall back to a raw ``==`` on the computed challenge. An
   accidental revert (e.g. a future refactor reaching for the obvious
   ``return computed == code_challenge``) trips this regression guard
   before it ships.
2. **Behavioral** — the function still returns the correct booleans for
   matched / mismatched / wrong-method inputs. The constant-time fix
   must not break the contract; the existing :func:`test_pkce_verifier_mismatch_returns_invalid_grant`
   in :mod:`tests.unit.test_oauth_token` covers the wider integration
   surface, this one pins ``_verify_pkce`` directly.
"""

from __future__ import annotations

import base64
import hashlib
import inspect

from vividscripts_mcp.oauth import token as token_module
from vividscripts_mcp.oauth.token import _verify_pkce


def _challenge_for(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def test_verify_pkce_uses_hmac_compare_digest() -> None:
    """Source-grep: the constant-time helper is the comparison primitive.

    Failing this test means a future patch reintroduced a non-constant-time
    string equality on the verifier path. The audit's #14 fix is structural —
    the assertion lives in the source, not the timing behavior.
    """
    source = inspect.getsource(_verify_pkce)
    assert "hmac.compare_digest" in source, (
        "_verify_pkce must use hmac.compare_digest for the verifier comparison (audit finding #14)"
    )


def test_verify_pkce_does_not_use_double_equals_on_computed() -> None:
    """A raw ``computed == code_challenge`` is the exact anti-pattern we are guarding against."""
    source = inspect.getsource(_verify_pkce)
    # Allow ``==`` elsewhere in the function (e.g. comparing the method
    # string), just not against the computed challenge.
    assert "computed == code_challenge" not in source
    assert "code_challenge == computed" not in source


def test_token_module_imports_hmac() -> None:
    """The fix introduces ``import hmac`` at the module level; pin it.

    Without this, a tester could ``hmac.compare_digest(...)`` via a
    lazy import and still leave the module-level surface inconsistent.
    """
    assert hasattr(token_module, "hmac"), "oauth.token must import hmac (audit finding #14)"


def test_verify_pkce_matches_correctly() -> None:
    """Regression guard: the constant-time fix preserves correctness."""
    verifier = "test-verifier-string-with-some-length"
    challenge = _challenge_for(verifier)
    assert _verify_pkce(verifier, challenge, "S256") is True


def test_verify_pkce_rejects_mismatch() -> None:
    verifier = "test-verifier-string-with-some-length"
    challenge = _challenge_for(verifier)
    assert _verify_pkce("a-different-verifier", challenge, "S256") is False


def test_verify_pkce_rejects_plain_method() -> None:
    """``plain`` was rejected at /oauth/authorize; the verifier must agree."""
    assert _verify_pkce("anything", "anything", "plain") is False
