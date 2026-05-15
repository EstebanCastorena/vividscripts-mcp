"""Workflow-state + custom-override tools (KAN-59).

Four user-scoped MCP tools:

- ``save_step_result`` — the gate between Claude Code's reasoning and
  persisted state. It schema-validates ``result`` against the step's
  canonical JSON Schema (KAN-57) **before** the backend is touched. A
  validation failure returns ``success=False`` + field-level
  ``validation_errors`` and persists nothing.
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
from vividscripts_mcp.schemas import validate_step_result


class CustomOverride(BaseModel):
    """Returned by get_custom_prompt_override."""

    model_config = ConfigDict(extra="forbid")

    has_override: bool
    template: str | None = None


class OverrideAck(BaseModel):
    """Returned by set_custom_prompt_override."""

    model_config = ConfigDict(extra="forbid")

    success: bool


def make_save_step_result_tool(
    backend: BackendProtocol,
) -> Callable[[str, str, dict[str, object]], StepResultOutcome]:
    """Build ``save_step_result`` bound to ``backend``."""

    def save_step_result(
        project_id: str,
        step_name: str,
        result: dict[str, object],
    ) -> StepResultOutcome:
        """Validate an AI step result against its schema, then persist it.

        Returns ``success=False`` with ``validation_errors`` (and saves
        nothing) when the result doesn't match the step's JSON Schema.
        On success returns the backend outcome including ``next_step``.
        """
        user_id = require_user_claims().sub
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
    """Register the four state tools on the FastMCP server."""
    mcp.tool()(make_save_step_result_tool(backend))
    mcp.tool()(make_get_workflow_state_tool(backend))
    mcp.tool()(make_get_custom_prompt_override_tool(backend))
    mcp.tool()(make_set_custom_prompt_override_tool(backend))
