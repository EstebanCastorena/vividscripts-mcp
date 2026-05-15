"""Full AI-pipeline integration test (KAN-60).

Drives every *in-pipeline* prompt end-to-end in dependency order:

  create_project
    → for each prompt (topologically ordered by depends_on):
        prompts/get(name, minimal-context)        # render works + schema embedded
        save_step_result(name, canned fixture)    # schema-validated + persisted
    → get_workflow_state shows every driven step completed

This is the Phase 2 acceptance test: "Claude Code completes all the
AI-driven steps end-to-end against MockBackend." It exercises the real
registered prompts, the real schema validation, and the real backend
state advancement — the OAuth/JSON-RPC wire itself is covered separately
by tests/integration/test_oauth_to_create_project.py, so this test uses
the in-process MCP API with the Bearer contextvar set (the same
authorization path, minus the redundant transport re-test).

Drift guard: the driven set is *derived* from PROMPT_INTERFACES, not
hardcoded. Add a 21st in-pipeline prompt and this test will try to drive
it, need its fixture, and fail loudly until the pipeline is updated —
exactly the KAN-60 acceptance criterion.
"""

from __future__ import annotations

import json
from collections import defaultdict, deque
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from vividscripts_mcp.adapters.mock import MockBackend
from vividscripts_mcp.oauth.bearer import UserClaims
from vividscripts_mcp.oauth.context import set_user_claims
from vividscripts_mcp.prompts import PROMPT_INTERFACES
from vividscripts_mcp.server import create_mcp_server

_FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "step_results"

# The two user-initiated tabs are not part of the linear story→video
# pipeline (their descriptions say so). Everything else is in-pipeline.
_OUT_OF_PIPELINE = {"story_optimization", "image_prompt_edit"}

# A representative multi-paragraph horror story. MockBackend is
# content-agnostic (it returns stub prompt bodies), so the exact prose
# doesn't affect assertions — it's here so the test reads like a real
# Claude Code session rather than `"x"`.
_STORY = (
    "I lived alone for years. Or so I thought.\n\n"
    "The house was too big for one person, but it was cheap, and cheap "
    "was all I could afford after the divorce. The previous owner had "
    "left in a hurry. The realtor wouldn't say why.\n\n"
    "The first month was quiet. Then the floor in the spare room began "
    "to creak at night. Settling, I told myself. Old houses settle.\n\n"
    "But settling doesn't sound like footsteps. Steady. Deliberate. "
    "Coming from the second bedroom — the one I'd nailed shut the day "
    "I moved in, because the door wouldn't stay closed on its own.\n\n"
    "Last night the footsteps stopped outside my door. And then, very "
    "softly, someone tried the handle."
)


def _topological_in_pipeline_order() -> list[str]:
    """Kahn's algorithm over PROMPT_INTERFACES.depends_on, restricted to
    in-pipeline prompts. Deterministic tie-break by name."""
    names = [n for n in PROMPT_INTERFACES if n not in _OUT_OF_PIPELINE]
    name_set = set(names)
    indeg: dict[str, int] = {n: 0 for n in names}
    children: dict[str, list[str]] = defaultdict(list)
    for n in names:
        for dep in PROMPT_INTERFACES[n].depends_on:
            if dep in name_set:  # ignore deps on out-of-pipeline prompts
                children[dep].append(n)
                indeg[n] += 1

    ready = deque(sorted(n for n, d in indeg.items() if d == 0))
    order: list[str] = []
    while ready:
        node = ready.popleft()
        order.append(node)
        for child in sorted(children[node]):
            indeg[child] -= 1
            if indeg[child] == 0:
                ready.append(child)
    assert len(order) == len(names), "cycle in in-pipeline depends_on graph"
    return order


def _minimal_context(step_name: str) -> dict[str, Any]:
    """Build a minimal schema-valid context dict for a prompt's
    input_schema (stub values typed to satisfy validation)."""
    schema = PROMPT_INTERFACES[step_name].input_schema
    ctx: dict[str, Any] = {}
    for field, spec in schema["properties"].items():
        field_type = spec.get("type", "string")
        if field_type == "integer":
            ctx[field] = int(spec.get("minimum", 1))
        elif field_type == "number":
            ctx[field] = float(spec.get("minimum", 1.0))
        elif field_type == "array":
            ctx[field] = []
        elif field_type == "object":
            ctx[field] = {}
        else:
            ctx[field] = f"stub-{field}"
    return ctx


def _fixture(step_name: str) -> dict[str, Any]:
    return json.loads((_FIXTURE_DIR / f"{step_name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def backend() -> MockBackend:
    return MockBackend(base_url="https://app.vividscripts.test")


@pytest.fixture
def authed() -> Iterator[None]:
    set_user_claims(
        UserClaims(
            sub="user-alpha",
            client_id="claude-code",
            scope=None,
            jti="jti",
            exp=2_000_000_000,
            iat=1_700_000_000,
        )
    )
    yield
    set_user_claims(None)


def _text(message: object) -> str:
    content = message.content  # type: ignore[attr-defined]
    return content.text if hasattr(content, "text") else str(content)


async def test_drive_full_ai_pipeline(backend: MockBackend, authed: None) -> None:
    """Walk create_project → every in-pipeline prompt → workflow state."""
    mcp = create_mcp_server(backend)

    # 1. Create the project (via the MCP tool, structured output).
    _content, project = await mcp.call_tool(
        "create_project",
        {
            "story": _STORY,
            "settings": {
                "style": "dark_cinematic",
                "voice": "female",
                "dimension": "landscape",
            },
        },
    )
    project_id = project["project_id"]
    assert project_id

    driven: list[str] = []
    order = _topological_in_pipeline_order()

    # 2. Drive every in-pipeline prompt in dependency order.
    for step_name in order:
        # 2a. prompts/get must render (stub body + embedded output schema).
        prompt_result = await mcp.get_prompt(step_name, _minimal_context(step_name))
        body = _text(prompt_result.messages[0])
        assert f'save_step_result(project_id, "{step_name}", result)' in body, (
            f"{step_name}: rendered prompt should reference its save call"
        )
        assert "```json" in body, f"{step_name}: output schema not embedded"

        # 2b. Claude Code produces the structured result — we use the
        # canned minimal-valid fixture as the stand-in.
        _c, outcome = await mcp.call_tool(
            "save_step_result",
            {
                "project_id": project_id,
                "step_name": step_name,
                "result": _fixture(step_name),
            },
        )
        assert outcome["success"] is True, (
            f"{step_name}: save_step_result failed: {outcome.get('validation_errors')}"
        )
        driven.append(step_name)

    # 3. Final workflow state reflects every driven step.
    _c, state = await mcp.call_tool("get_workflow_state", {"project_id": project_id})
    completed = set(state["completed_steps"])
    missing = set(driven) - completed
    assert not missing, f"steps driven but not in completed_steps: {sorted(missing)}"
    assert state["status"] == "in_progress"


def test_pipeline_covers_every_in_pipeline_prompt() -> None:
    """Drift guard: the driven order must be exactly the in-pipeline set.

    If a 21st in-pipeline prompt is added to PROMPT_INTERFACES, it lands
    in this order automatically; the E2E test above will then demand a
    fixture for it and fail until the pipeline is genuinely updated.
    """
    order = _topological_in_pipeline_order()
    expected = set(PROMPT_INTERFACES) - _OUT_OF_PIPELINE
    assert set(order) == expected
    assert len(order) == len(expected)
    # story_blueprint has no in-pipeline deps → must be first.
    assert order[0] == "story_blueprint"


def test_every_in_pipeline_prompt_has_a_fixture() -> None:
    """Each in-pipeline prompt needs a canned result for the E2E drive."""
    for step_name in _topological_in_pipeline_order():
        path = _FIXTURE_DIR / f"{step_name}.json"
        assert path.is_file(), f"missing canned fixture for {step_name}"


def test_dependencies_precede_dependents_in_drive_order() -> None:
    """A prompt is only driven after all its in-pipeline deps were driven."""
    order = _topological_in_pipeline_order()
    position = {name: i for i, name in enumerate(order)}
    for name in order:
        for dep in PROMPT_INTERFACES[name].depends_on:
            if dep in position:
                assert position[dep] < position[name], f"{name} driven before its dependency {dep}"
