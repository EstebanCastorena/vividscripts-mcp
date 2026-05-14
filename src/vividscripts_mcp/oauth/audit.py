"""Structured audit event logging for the OAuth surface.

Phase 1 emits JSON lines to the standard logger under
``vividscripts_mcp.audit``. Production deploys can route this logger to
CloudWatch or any structured log sink without code changes.

Callers MUST redact sensitive material (Bearer tokens, authorization
codes, ``code_verifier`` values) before passing them into ``fields``.
This helper performs no redaction of its own — that's an explicit choice
to keep the policy where it belongs: at the call site where the
sensitivity of each field is unambiguous.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

_logger = logging.getLogger("vividscripts_mcp.audit")


def emit_audit_event(event_type: str, **fields: Any) -> None:
    """Emit one structured audit log line."""
    record: dict[str, Any] = {
        "ts": datetime.now(UTC).isoformat(),
        "event": event_type,
        **fields,
    }
    _logger.info(json.dumps(record, default=str))
