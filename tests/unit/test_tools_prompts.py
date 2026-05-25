"""Tests for the MCP Prompts wire + list_workflow_steps (KAN-58)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from vividscripts_mcp.adapters.mock import MockBackend
from vividscripts_mcp.models import StepDefinition
from vividscripts_mcp.oauth.bearer import UserClaims
from vividscripts_mcp.oauth.context import set_user_claims
from vividscripts_mcp.prompts import PROMPT_INTERFACES
from vividscripts_mcp.server import create_mcp_server


def _claims(sub: str = "user-alpha") -> UserClaims:
    return UserClaims(
        sub=sub,
        client_id="test-client",
        scope=None,
        jti="jti",
        exp=2_000_000_000,
        iat=1_700_000_000,
    )


@pytest.fixture
def backend() -> MockBackend:
    return MockBackend(base_url="https://app.vividscripts.test")


@pytest.fixture
def authed() -> Iterator[None]:
    set_user_claims(_claims())
    yield
    set_user_claims(None)


def _text(message: object) -> str:
    content = message.content  # type: ignore[attr-defined]
    return content.text if hasattr(content, "text") else str(content)


# ---------------------------------------------------------------------------
# prompts/list
# ---------------------------------------------------------------------------


async def test_all_20_prompts_registered(backend: MockBackend) -> None:
    """20 prompts: the 19 pipeline + user-tab interfaces plus the
    KAN-127 ``resume_project`` documentation prompt."""
    mcp = create_mcp_server(backend)
    prompts = await mcp.list_prompts()
    names = {p.name for p in prompts}
    assert names == set(PROMPT_INTERFACES.keys())
    assert len(prompts) == 20


async def test_prompt_arguments_mirror_input_schema(backend: MockBackend) -> None:
    """Each registered prompt's arguments + required flags match its interface."""
    mcp = create_mcp_server(backend)
    prompts = {p.name: p for p in await mcp.list_prompts()}

    for name, interface in PROMPT_INTERFACES.items():
        prompt = prompts[name]
        arg_names = {a.name for a in (prompt.arguments or [])}
        assert arg_names == set(interface.input_schema["properties"].keys())

        required_args = {a.name for a in (prompt.arguments or []) if a.required}
        assert required_args == set(interface.input_schema.get("required", []))


async def test_prompt_descriptions_match_interface(backend: MockBackend) -> None:
    mcp = create_mcp_server(backend)
    prompts = {p.name: p for p in await mcp.list_prompts()}
    for name, interface in PROMPT_INTERFACES.items():
        assert prompts[name].description == interface.description


# ---------------------------------------------------------------------------
# prompts/get
# ---------------------------------------------------------------------------


async def test_get_prompt_returns_body_plus_output_schema(
    backend: MockBackend, authed: None
) -> None:
    mcp = create_mcp_server(backend)
    result = await mcp.get_prompt("story_summarizer", {"story": "A man, alone."})
    assert len(result.messages) == 1
    body = _text(result.messages[0])
    # Stub body from MockBackend
    assert "[MOCK PROMPT for story_summarizer]" in body
    # save_step_result call hint
    assert 'save_step_result(project_id, "story_summarizer", result)' in body
    # Embedded canonical output schema (short_summary is required there)
    assert "short_summary" in body
    assert "```json" in body


async def test_get_prompt_requires_authentication(backend: MockBackend) -> None:
    """No Bearer context → render fails. FastMCP wraps the AuthRequired in a
    ValueError, so the wire-level failure carries the auth message."""
    set_user_claims(None)
    mcp = create_mcp_server(backend)
    with pytest.raises(Exception) as exc:
        await mcp.get_prompt("story_summarizer", {"story": "x"})
    assert "authenticated Bearer context" in str(exc.value)


async def test_get_prompt_honors_custom_override(backend: MockBackend, authed: None) -> None:
    """A user override stored in the backend is what prompts/get renders."""
    backend.set_custom_prompt_override(
        user_id="user-alpha",
        step_name="title_generator",
        template="MY CUSTOM TITLE PROMPT — be punchy.",
    )
    mcp = create_mcp_server(backend)
    result = await mcp.get_prompt(
        "title_generator",
        {"hook_brief": "a hook", "style_anchors": "noir"},
    )
    body = _text(result.messages[0])
    assert "MY CUSTOM TITLE PROMPT" in body
    # The override replaces the body but the schema block still appended
    assert "```json" in body


async def test_get_prompt_rejects_missing_required_context(
    backend: MockBackend, authed: None
) -> None:
    """story_summarizer requires `story`; omitting it fails before the backend."""
    mcp = create_mcp_server(backend)
    with pytest.raises(Exception) as exc:
        await mcp.get_prompt("story_summarizer", {})
    assert "story" in str(exc.value).lower()


async def test_get_prompt_rejects_wrong_typed_context(backend: MockBackend, authed: None) -> None:
    """paragraph_count is an integer; a string must be rejected pre-format."""
    mcp = create_mcp_server(backend)
    with pytest.raises(Exception) as exc:
        await mcp.get_prompt(
            "story_blueprint",
            {
                "story": "s",
                "numbered_story": "1. s",
                "paragraph_count": "not-an-int",
            },
        )
    assert "paragraph_count" in str(exc.value)


# ---------------------------------------------------------------------------
# Documentation prompts (KAN-127)
# ---------------------------------------------------------------------------


async def test_documentation_prompt_returns_inline_body(backend: MockBackend, authed: None) -> None:
    """resume_project ships a public runbook body and bypasses the
    backend ``format_prompt`` + ``save_step_result`` suffix path."""
    mcp = create_mcp_server(backend)
    result = await mcp.get_prompt("resume_project", {})
    assert len(result.messages) == 1
    body = _text(result.messages[0])
    # The runbook landmarks the body promises (covered by the unit test
    # too, but pinned here for the wire surface).
    assert "Resume a project after a dropped MCP session" in body
    assert "list_projects()" in body
    assert "get_workflow_state(project_id)" in body
    # And — most importantly — the doc prompt does NOT carry the
    # template-prompt save_step_result trailer that would tell Claude to
    # call save_step_result for the runbook itself.
    assert "save_step_result(project_id," not in body
    # Nor does it embed the canonical output schema block.
    assert "```json" not in body
    # Nor does it ever invoke the backend (would have surfaced the
    # MockBackend stub marker).
    assert "[MOCK PROMPT" not in body


async def test_documentation_prompt_accepts_optional_context_hint(
    backend: MockBackend, authed: None
) -> None:
    """The optional ``context_hint`` argument is accepted but does not
    change the rendered runbook (the body is verbatim)."""
    mcp = create_mcp_server(backend)
    no_hint = await mcp.get_prompt("resume_project", {})
    with_hint = await mcp.get_prompt(
        "resume_project",
        {"context_hint": "the haunted-doorbell story"},
    )
    assert _text(no_hint.messages[0]) == _text(with_hint.messages[0])


async def test_documentation_prompt_still_requires_authentication(
    backend: MockBackend,
) -> None:
    """Doc prompts skip the backend round-trip but still require Bearer."""
    set_user_claims(None)
    mcp = create_mcp_server(backend)
    with pytest.raises(Exception) as exc:
        await mcp.get_prompt("resume_project", {})
    assert "authenticated Bearer context" in str(exc.value)


# ---------------------------------------------------------------------------
# list_workflow_steps tool (no longer a stub)
# ---------------------------------------------------------------------------


async def test_list_workflow_steps_returns_real_catalog(backend: MockBackend) -> None:
    """The Phase 1 `return []` stub is gone; the tool serves the backend."""
    mcp = create_mcp_server(backend)
    _content, structured = await mcp.call_tool("list_workflow_steps", {})
    steps = structured["result"]
    assert isinstance(steps, list)
    assert len(steps) > 0, "list_workflow_steps must no longer return []"


def test_list_workflow_steps_backend_contract(backend: MockBackend) -> None:
    """Direct backend check: the catalog is non-empty StepDefinitions."""
    steps = backend.list_workflow_steps()
    assert steps
    assert all(isinstance(s, StepDefinition) for s in steps)


async def test_list_workflow_steps_registered_as_tool(backend: MockBackend) -> None:
    mcp = create_mcp_server(backend)
    tool_names = {t.name for t in await mcp.list_tools()}
    assert "list_workflow_steps" in tool_names
    # The Phase 1/KAN-53 project tools are still there
    assert {"create_project", "list_projects", "get_project"} <= tool_names
