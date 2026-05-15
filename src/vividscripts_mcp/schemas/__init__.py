"""Step-result JSON Schemas + the validation helper (KAN-57).

One ``<step_name>.json`` file per prompt, JSON Schema Draft 2020-12,
derived from auditing the output-parsing code in slide_editor's
processors (2026-05-14). The schemas catch wrong *types* and missing
*critical* fields; they intentionally leave ``additionalProperties``
permissive because every processor reads fields via ``.get()`` with
defaults and ignores unknown keys — forbidding extras would reject
valid production outputs that carry extra commentary fields.

:func:`validate_step_result` is what KAN-59's ``save_step_result`` tool
calls before persisting an AI response. It returns ``(ok, errors)``
with dotted error paths, e.g.
``paragraph_analyses.2.tension: 'high' is not of type 'integer'``.
"""

from __future__ import annotations

import json
from functools import cache
from importlib import resources

import jsonschema

__all__ = ["KNOWN_STEPS", "validate_step_result"]


@cache
def _load_schema(step_name: str) -> dict[str, object] | None:
    """Load + cache one step schema. ``None`` if the file doesn't exist."""
    resource = resources.files("vividscripts_mcp.schemas") / f"{step_name}.json"
    if not resource.is_file():
        return None
    data: dict[str, object] = json.loads(resource.read_text(encoding="utf-8"))
    return data


@cache
def _known_steps() -> frozenset[str]:
    root = resources.files("vividscripts_mcp.schemas")
    return frozenset(
        entry.name.removesuffix(".json") for entry in root.iterdir() if entry.name.endswith(".json")
    )


#: The step names that have a registered output schema. Equals the 20
#: PROMPT_INTERFACES names (enforced by a cross-test in
#: tests/unit/test_schemas.py).
KNOWN_STEPS: frozenset[str] = _known_steps()


def validate_step_result(
    step_name: str,
    result: dict[str, object],
) -> tuple[bool, list[str]]:
    """Validate an AI step result against its schema.

    Returns ``(True, [])`` on success, ``(False, [messages])`` otherwise.
    An unknown ``step_name`` is a validation failure (not an exception)
    so the calling tool can return a clean 400 rather than a 500.
    """
    schema = _load_schema(step_name)
    if schema is None:
        return False, [f"unknown step: {step_name!r}"]

    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(result), key=lambda e: list(e.absolute_path))
    if not errors:
        return True, []

    messages = []
    for err in errors:
        path = ".".join(str(p) for p in err.absolute_path) or "<root>"
        messages.append(f"{path}: {err.message}")
    return False, messages
