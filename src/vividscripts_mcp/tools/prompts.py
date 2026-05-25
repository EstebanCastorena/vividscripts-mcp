"""MCP Prompts wire + list_workflow_steps tool (KAN-58).

The :data:`PROMPT_INTERFACES` entries are registered as native MCP
Prompts. In Claude Code they surface as ``/slash-commands``; Claude Code
also calls them programmatically via ``prompts/get``.

``prompts/get`` flow (template prompts):

1. The provided context is schema-validated against the prompt's
   ``input_schema`` (KAN-56) — bad context fails *before* the backend
   is touched.
2. The backend renders the body (:meth:`format_prompt`). Against
   ``MockBackend`` this is stub text; the real ``VividScriptsAdapter``
   renders the actual template in Phase 3. Custom user overrides are
   the backend's responsibility (``MockBackend.format_prompt`` already
   prefers a stored override).
3. The canonical output schema (KAN-57, ``schemas/<name>.json``) is
   appended to the message so Claude Code knows exactly what shape to
   return and that ``save_step_result`` will validate it.

``prompts/get`` flow (documentation prompts — KAN-127):

An interface with ``body`` set is a self-contained operational
runbook. The body is rendered verbatim — no backend round-trip, no
``save_step_result`` suffix. Input is still schema-validated so
unknown kwargs surface as errors. ``resume_project`` is the first
documentation prompt; the renderer here is generic.

``list_workflow_steps`` is wired to the backend here, replacing the
Phase 1 ``return []`` stub.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import jsonschema
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.prompts.base import Prompt, PromptArgument, UserMessage

from vividscripts_mcp.adapters.base import BackendProtocol
from vividscripts_mcp.models import StepDefinition
from vividscripts_mcp.oauth.context import require_user_claims
from vividscripts_mcp.prompts import PROMPT_INTERFACES, PromptInterface
from vividscripts_mcp.schemas import get_output_schema


def _build_prompt(backend: BackendProtocol, interface: PromptInterface) -> Prompt:
    """Construct one MCP Prompt bound to ``backend`` for ``interface``."""
    properties: dict[str, dict[str, object]] = interface.input_schema["properties"]
    required = set(interface.input_schema.get("required", []))
    arguments = [
        PromptArgument(
            name=field,
            description=str(spec.get("description", "")),
            required=field in required,
        )
        for field, spec in properties.items()
    ]

    input_validator = jsonschema.Draft202012Validator(interface.input_schema)
    output_schema = get_output_schema(interface.name) or {"type": "object"}
    output_schema_json = json.dumps(output_schema, indent=2)
    step_name = interface.name
    documentation_body = interface.body

    def render(**kwargs: object) -> list[UserMessage]:
        context = dict(kwargs)
        errors = sorted(
            input_validator.iter_errors(context),
            key=lambda e: list(e.absolute_path),
        )
        if errors:
            detail = "; ".join(
                f"{'.'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
                for e in errors
            )
            raise ValueError(f"invalid context for {step_name}: {detail}")

        # Documentation prompts (KAN-127) ship a public runbook body
        # and skip the backend round-trip + save_step_result suffix.
        # Auth is still required so the surface stays uniform.
        require_user_claims()
        if documentation_body is not None:
            return [UserMessage(documentation_body)]

        user_id = require_user_claims().sub
        payload = backend.format_prompt(
            user_id=user_id,
            step_name=step_name,
            context=context,
        )
        body = (
            f"{payload.prompt}\n\n"
            "---\n"
            f'When done, call save_step_result(project_id, "{step_name}", result) '
            "where `result` validates against this JSON Schema:\n\n"
            f"```json\n{output_schema_json}\n```\n\n"
            f"{payload.instructions}"
        )
        return [UserMessage(body)]

    return Prompt(
        name=interface.name,
        title=None,
        description=interface.description,
        arguments=arguments,
        fn=render,
        icons=None,
        context_kwarg=None,
    )


def make_list_workflow_steps_tool(
    backend: BackendProtocol,
) -> Callable[[], list[StepDefinition]]:
    """Build the ``list_workflow_steps`` tool bound to ``backend``.

    Replaces Phase 1's empty-list stub. No auth scoping — the workflow
    catalog is the same for every user.
    """

    def list_workflow_steps() -> list[StepDefinition]:
        """List the VividScripts workflow steps with metadata."""
        return backend.list_workflow_steps()

    return list_workflow_steps


def register_prompts(mcp: FastMCP, backend: BackendProtocol) -> None:
    """Register every MCP Prompt + the list_workflow_steps tool."""
    for interface in PROMPT_INTERFACES.values():
        mcp.add_prompt(_build_prompt(backend, interface))
    mcp.tool()(make_list_workflow_steps_tool(backend))
