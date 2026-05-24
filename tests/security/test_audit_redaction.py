"""Sec-E / KAN-98 — defense-in-depth audit-log scrubber (audit finding #16).

The Phase-1 design pushed redaction to call sites: every caller of
:func:`emit_audit_event` is expected to redact its own tokens / codes /
``code_verifier`` values before passing them in. That keeps redaction
*intentional* — the call site is the place where field sensitivity is
unambiguous — but it has a known weakness: a single forgetful caller
silently leaks a secret into the structured log stream, and the design
provides no backstop.

The audit's recommendation is a defense-in-depth scrubber on the emit
side that fingerprints/drops any field whose **key** matches a known
sensitive pattern (case-insensitive, substring), independent of the
caller's diligence. The key allow-list is intentionally tight — adding
to it should be a deliberate choice, not a "we noticed".
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

import pytest

from vividscripts_mcp.oauth.audit import emit_audit_event


@pytest.fixture
def captured(caplog: pytest.LogCaptureFixture) -> Iterator[list[str]]:
    """Capture audit log lines as raw strings."""
    caplog.set_level(logging.INFO, logger="vividscripts_mcp.audit")
    lines: list[str] = []
    yield lines
    lines.extend(r.getMessage() for r in caplog.records if r.name == "vividscripts_mcp.audit")


def _emit_and_collect(
    caplog: pytest.LogCaptureFixture, event_type: str, **fields: object
) -> tuple[str, dict[str, object]]:
    """Emit an event and return (raw_line, parsed_json) for the latest record."""
    caplog.clear()
    caplog.set_level(logging.INFO, logger="vividscripts_mcp.audit")
    emit_audit_event(event_type, **fields)
    records = [r for r in caplog.records if r.name == "vividscripts_mcp.audit"]
    assert records, "audit logger emitted no record"
    line = records[-1].getMessage()
    return line, json.loads(line)


# ---------------------------------------------------------------------
# Sensitive keys are redacted regardless of caller discipline
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "access_token",
        "refresh_token",
        "id_token",
        "bearer_token",
        "token",
        "client_secret",
        "secret",
        "code_verifier",
        "authorization",
        "password",
        "passphrase",
        "cookie",
        "session_cookie",
        "vs_session",  # the seed-session cookie name pattern
    ],
)
def test_sensitive_key_value_never_appears_raw(key: str, caplog: pytest.LogCaptureFixture) -> None:
    """A caller that forgets to redact must still not leak the secret.

    The exact value the caller passed in must not be reconstructible
    from the emitted line. Acceptable replacements: a fingerprint
    (``"sha256:..."``), a literal ``"<redacted>"`` placeholder, or
    omission of the field entirely.
    """
    canary = "S3CRET-canary-VALUE-do-not-leak-abc123"
    line, _parsed = _emit_and_collect(caplog, "test.event", **{key: canary})
    assert canary not in line, f"audit log leaked raw value for sensitive key {key!r}: {line!r}"


@pytest.mark.parametrize(
    "key",
    [
        "ACCESS_TOKEN",
        "Refresh_Token",
        "Authorization",
        "Code_Verifier",
        "Client_Secret",
        "Cookie",
    ],
)
def test_sensitive_key_match_is_case_insensitive(
    key: str, caplog: pytest.LogCaptureFixture
) -> None:
    canary = "S3CRET-canary-VALUE-abc123"
    line, _parsed = _emit_and_collect(caplog, "test.event", **{key: canary})
    assert canary not in line


def test_sensitive_key_substring_match_catches_namespaced_keys(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``cognito_access_token`` / ``cognito_id_token`` are still tokens.

    Substring matching against the sensitive-key alphabet means a caller
    can't sneak the leak through by prefixing.
    """
    canary = "S3CRET-canary-abc"
    line, _parsed = _emit_and_collect(
        caplog,
        "oauth.callback",
        cognito_access_token=canary,
        cognito_refresh_token=canary,
        my_authorization_header=canary,
    )
    assert canary not in line


# ---------------------------------------------------------------------
# Safe keys still pass through (regression guard)
# ---------------------------------------------------------------------


def test_safe_keys_passthrough_unchanged(caplog: pytest.LogCaptureFixture) -> None:
    """The scrubber must not drop the keys we want to keep auditable."""
    _line, parsed = _emit_and_collect(
        caplog,
        "oauth.token.issued",
        client_id="claude-code-001",
        user_id="user-alpha",
        grant_type="authorization_code",
        via="cognito",
    )
    assert parsed["client_id"] == "claude-code-001"
    assert parsed["user_id"] == "user-alpha"
    assert parsed["grant_type"] == "authorization_code"
    assert parsed["via"] == "cognito"
    # And the event metadata is preserved.
    assert parsed["event"] == "oauth.token.issued"
    assert "ts" in parsed


def test_event_type_not_scrubbed(caplog: pytest.LogCaptureFixture) -> None:
    """Even if the event type literally contains ``token``, it stays in the line."""
    _line, parsed = _emit_and_collect(caplog, "oauth.token.issued", user_id="user-alpha")
    assert parsed["event"] == "oauth.token.issued"


# ---------------------------------------------------------------------
# CRLF / control-char neutralization is preserved
# ---------------------------------------------------------------------


def test_control_chars_in_value_are_escaped(caplog: pytest.LogCaptureFixture) -> None:
    """``json.dumps`` neutralizes ``\\n``/``\\r`` — the scrubber must not regress that.

    The existing design relied on ``json.dumps`` for the audit-log
    value-escape guarantee; the new scrubber's regex must not bypass it
    by emitting unescaped passthroughs.
    """
    line, _parsed = _emit_and_collect(
        caplog,
        "test.event",
        user_id="user\nALPHA\rNEWLINE",
        client_id="client\nID",
    )
    # The emitted line is a single JSON object — no literal newlines from
    # the values should break onto a new physical line.
    assert "\n" not in line
    assert "\r" not in line
