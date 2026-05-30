"""Workflow-state + custom-override tools (KAN-59, KAN-106).

Five MCP tools (four user-scoped + the unauthenticated
``get_step_schema``):

- ``save_step_result`` — the gate between Claude Code's reasoning and
  persisted state. It schema-validates ``result`` against the step's
  canonical JSON Schema (KAN-57) **before** the backend is touched. A
  validation failure returns ``success=False`` + field-level
  ``validation_errors`` and persists nothing.
- ``get_step_schema`` — surfaces the JSON Schema a step's ``result``
  must satisfy, so a caller can learn the exact fields and types up
  front instead of discovering them through ``save_step_result``
  validation errors (KAN-106). Unauthenticated: the schema catalog is
  static and public (it ships in this repo), the same for every caller
  — consistent with ``list_workflow_steps``.
- ``get_workflow_state`` — current pipeline position, enough to resume
  mid-workflow.
- ``get_custom_prompt_override`` / ``set_custom_prompt_override`` — a
  user's per-prompt template override. ``set`` rejects step names that
  aren't one of the 20 known prompts (an unknown override would be dead
  weight the backend could never serve).

Namespace note: ``step_name`` here is a *prompt* name (the 20
PROMPT_INTERFACES / schema keys), not a coarser WORKFLOW_STEPS pipeline
name. ``validate_step_result`` is the authoritative gate; the backend
just persists. Phase 3's real adapter must map the prompt namespace to
whatever its ProjectManager expects — flagged on KAN-31.
"""

from __future__ import annotations

from collections.abc import Callable

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from vividscripts_mcp.adapters.base import BackendProtocol
from vividscripts_mcp.models import StepResultOutcome, WorkflowState
from vividscripts_mcp.oauth.context import require_user_claims
from vividscripts_mcp.prompts import PROMPT_INTERFACES
from vividscripts_mcp.schemas import KNOWN_STEPS, get_output_schema, validate_step_result

# KAN-97 #10 — bound the custom-prompt template. 50_000 chars covers any
# legitimate template (the longest shipped prompt is ~3k chars) while
# refusing memory-exhaustion payloads.
_MAX_TEMPLATE_CHARS = 50_000


class CustomOverride(BaseModel):
    """Returned by get_custom_prompt_override."""

    model_config = ConfigDict(extra="forbid")

    has_override: bool
    template: str | None = None


class OverrideAck(BaseModel):
    """Returned by set_custom_prompt_override."""

    model_config = ConfigDict(extra="forbid")

    success: bool


class StepSchema(BaseModel):
    """Returned by get_step_schema (KAN-106).

    ``found`` is True when ``step_name`` matched a known step and
    ``json_schema`` carries the JSON Schema its ``result`` must satisfy.
    When ``step_name`` is omitted or unknown, ``found`` is False,
    ``json_schema`` is None, and ``known_steps`` lists every valid name
    so the caller can self-correct without another round-trip.
    """

    model_config = ConfigDict(extra="forbid")

    step_name: str | None
    found: bool
    json_schema: dict[str, object] | None
    known_steps: list[str]


def get_step_schema(step_name: str | None = None) -> StepSchema:
    """Return the JSON Schema a step's ``result`` must satisfy in save_step_result.

    Call this *before* ``save_step_result`` to learn the exact required
    fields and types instead of discovering them through validation
    errors. Field names are deliberately inconsistent across steps (they
    mirror the backend processors — e.g. ``image_instruction`` for
    ``image_director_first`` but ``image_prompt`` for ``thumbnail``), so
    checking the schema is the reliable path rather than guessing.

    Omit ``step_name`` — or pass an unknown one — to get every valid step
    name back in ``known_steps``. No auth required; the catalog is the
    same for every caller.
    """
    known = sorted(KNOWN_STEPS)
    if step_name is None:
        return StepSchema(
            step_name=None,
            found=False,
            json_schema=None,
            known_steps=known,
        )
    schema = get_output_schema(step_name)
    return StepSchema(
        step_name=step_name,
        found=schema is not None,
        json_schema=schema,
        known_steps=known,
    )


def make_save_step_result_tool(
    backend: BackendProtocol,
) -> Callable[[str, str, dict[str, object], int | None], StepResultOutcome]:
    """Build ``save_step_result`` bound to ``backend``."""

    def save_step_result(
        project_id: str,
        step_name: str,
        result: dict[str, object],
        scene_index: int | None = None,
    ) -> StepResultOutcome:
        """Validate an AI step result against its schema, then persist it.

        Call ``get_step_schema(step_name)`` first to see the exact
        required fields and types — field naming is inconsistent across
        steps, so guessing wastes round-trips.

        Returns ``success=False`` with ``validation_errors`` (and saves
        nothing) when the result doesn't match the step's JSON Schema.
        On success returns the backend outcome including ``next_step``.

        ``scene_index`` (KAN-90): omit for single-valued steps. For a
        per-scene/looped step (e.g. per-scene image directions), pass
        the 0-based scene index — results accumulate per scene rather
        than overwriting. A step must be used one way consistently.
        """
        user_id = require_user_claims().sub
        if scene_index is not None and scene_index < 0:
            return StepResultOutcome(
                success=False,
                validation_errors=["scene_index must be >= 0"],
                next_step=None,
            )
        ok, errors = validate_step_result(step_name, result)
        if not ok:
            return StepResultOutcome(
                success=False,
                validation_errors=errors,
                next_step=None,
            )
        return backend.save_step_result(
            user_id=user_id,
            project_id=project_id,
            step_name=step_name,
            result=result,
            scene_index=scene_index,
        )

    return save_step_result


def make_get_workflow_state_tool(
    backend: BackendProtocol,
) -> Callable[[str], WorkflowState]:
    """Build ``get_workflow_state`` bound to ``backend``."""

    def get_workflow_state(project_id: str) -> WorkflowState:
        """Return the project's current workflow state (status, completed
        steps, current step, accumulated data)."""
        user_id = require_user_claims().sub
        return backend.get_workflow_state(user_id=user_id, project_id=project_id)

    return get_workflow_state


def make_get_custom_prompt_override_tool(
    backend: BackendProtocol,
) -> Callable[[str], CustomOverride]:
    """Build ``get_custom_prompt_override`` bound to ``backend``."""

    def get_custom_prompt_override(step_name: str) -> CustomOverride:
        """Return the caller's custom template for ``step_name`` if set."""
        user_id = require_user_claims().sub
        template = backend.get_custom_prompt_override(user_id=user_id, step_name=step_name)
        return CustomOverride(
            has_override=template is not None,
            template=template,
        )

    return get_custom_prompt_override


def make_set_custom_prompt_override_tool(
    backend: BackendProtocol,
) -> Callable[[str, str], OverrideAck]:
    """Build ``set_custom_prompt_override`` bound to ``backend``."""

    def set_custom_prompt_override(step_name: str, template: str) -> OverrideAck:
        """Store a custom template for one of the 20 known prompts.

        Rejects unknown step names — a custom override for a prompt that
        doesn't exist could never be served and would just accumulate.
        """
        if len(template) > _MAX_TEMPLATE_CHARS:
            raise ValueError(f"template is {len(template)} chars; max is {_MAX_TEMPLATE_CHARS}")
        user_id = require_user_claims().sub
        if step_name not in PROMPT_INTERFACES:
            raise ValueError(
                f"unknown prompt {step_name!r}; "
                f"must be one of the {len(PROMPT_INTERFACES)} registered prompts"
            )
        backend.set_custom_prompt_override(user_id=user_id, step_name=step_name, template=template)
        return OverrideAck(success=True)

    return set_custom_prompt_override


def register_state_tools(mcp: FastMCP, backend: BackendProtocol) -> None:
    """Register the state tools on the FastMCP server."""
    mcp.tool()(make_save_step_result_tool(backend))
    mcp.tool()(get_step_schema)
    mcp.tool()(make_get_workflow_state_tool(backend))
    mcp.tool()(make_get_custom_prompt_override_tool(backend))
    mcp.tool()(make_set_custom_prompt_override_tool(backend))
