"""Structured audit event logging for the OAuth surface.

Phase 1 emits JSON lines to the standard logger under
``vividscripts_mcp.audit``. Production deploys can route this logger to
CloudWatch or any structured log sink without code changes.

Call sites are responsible for redacting their own sensitive material —
that keeps the policy where field sensitivity is unambiguous. But a
single forgetful caller silently leaks a token / code / cookie into the
structured stream, so :func:`emit_audit_event` also applies a
defense-in-depth scrubber on emit (KAN-98 #16). Any field whose key
matches a known sensitive substring (case-insensitive) is replaced with
a stable SHA-256 fingerprint before the line is serialized.

``json.dumps`` continues to neutralize CRLF / control characters in
field values; the scrubber does not bypass that guarantee.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

_logger = logging.getLogger("vividscripts_mcp.audit")

#: KAN-98 #16 — defense-in-depth backstop. Any field whose key matches
#: this pattern (case-insensitive, substring) is replaced with a stable
#: SHA-256 fingerprint on emit, regardless of caller discipline. Pattern
#: list is intentionally tight — adding to it should be a deliberate
#: choice, not a "we noticed".
_SENSITIVE_KEY_PATTERN = re.compile(
    r"token|secret|code_verifier|authoriz|password|passphrase|cookie|session",
    re.IGNORECASE,
)


def _is_sensitive_key(key: str) -> bool:
    return bool(_SENSITIVE_KEY_PATTERN.search(key))


def _fingerprint(value: Any) -> str:
    """Return a non-reversible fingerprint of ``value``.

    Stable across calls within a process so two log lines about the same
    leaked secret correlate; the raw value is unrecoverable.
    """
    digest = hashlib.sha256(repr(value).encode("utf-8", errors="replace")).hexdigest()
    return f"sha256:{digest[:16]}"


def _redact_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Replace sensitive values with fingerprints in-place on a copy."""
    out: dict[str, Any] = {}
    for key, value in fields.items():
        if _is_sensitive_key(key):
            out[key] = _fingerprint(value)
        else:
            out[key] = value
    return out


def emit_audit_event(event_type: str, **fields: Any) -> None:
    """Emit one structured audit log line.

    Sensitive field values are fingerprinted on the way out (KAN-98 #16).
    """
    record: dict[str, Any] = {
        "ts": datetime.now(UTC).isoformat(),
        "event": event_type,
        **_redact_fields(fields),
    }
    _logger.info(json.dumps(record, default=str))
