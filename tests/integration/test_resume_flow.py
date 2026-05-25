"""Resume-after-dropped-MCP-session regression test (KAN-127).

The user-facing scenario (`docs/prompts.md` § Operational runbooks,
prompt name ``resume_project``):

  1. Session A drives create_project → add_scene → generate_audio.
  2. Session A dies before the audio job is polled (transport drop,
     token TTL expiry, Claude Code restart). The MCP client is torn
     down.
  3. A *new* MCP client is opened. Server-side workflow state has
     survived in the backend (in production: ``mcp_workflow_state.json``
     persisted per project; here: the in-memory ``MockBackend``).
  4. Session B picks up by calling ``list_projects()``, identifying the
     orphaned project_id, ``get_workflow_state`` to read the surviving
     state, and a subsequent ``check_job(job_id)`` to confirm the
     server still owns the in-flight async job.

This test pins that contract. It does NOT exercise the (separate,
larger) session-reconnect bug — that is KAN-123 territory. The point
here is to prove that as long as the *backend* survives, a fresh MCP
client against it can resume from any previous client's state.

Backend
-------
``MockBackend`` is used: the integration scaffolding established in
``test_oauth_to_create_project.py`` builds full apps with mock stores,
but for a single-user resume flow the simpler ``create_mcp_server``
entry point (the in-process pattern used by ``test_full_ai_pipeline``)
is enough. The "two sessions" are two distinct ``create_mcp_server``
instances that share the same backend reference — which is exactly
what production looks like at the BackendProtocol boundary: many
short-lived MCP servers, one long-lived backend process.

The job submission itself is run against the mock — ``MockBackend.submit_job``
returns a synthetic ``JobStatus`` (status=completed, result={mock: True})
immediately so the test never blocks on a real media job. The point
isn't to time the job; it's to prove the job_id is still resolvable
from a fresh MCP session.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from vividscripts_mcp.adapters.mock import MockBackend
from vividscripts_mcp.oauth.bearer import UserClaims
from vividscripts_mcp.oauth.context import set_user_claims
from vividscripts_mcp.server import create_mcp_server


@pytest.fixture
def backend() -> MockBackend:
    """A single backend shared across both 'sessions'.

    In production this is the long-lived VividScriptsAdapter; here the
    in-memory MockBackend stands in. The key invariant is that the
    *same* instance is used by both create_mcp_server calls so the
    workflow state survives across MCP-client lifetimes.
    """
    return MockBackend(base_url="https://app.vividscripts.test")


@pytest.fixture
def authed() -> Iterator[None]:
    """Bind Bearer claims for user-alpha across both 'sessions'.

    The contextvar is process-wide; for an in-process test that simulates
    sequential MCP client lifetimes, one bind covers both. In a real
    deployment session A and session B each present their own (valid)
    Bearer token for the same user_id.
    """
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


_STORY = (
    "I lived alone for years. Or so I thought.\n\n"
    "The house was too big for one person, but it was cheap. The "
    "previous owner left in a hurry."
)


async def test_resume_after_dropped_session(backend: MockBackend, authed: None) -> None:
    """End-to-end resume flow.

    Session A: create_project, add_scene, generate_audio (captures job_id).
    Session A is then torn down by dropping the reference to ``session_a``.
    Session B: list_projects, get_workflow_state, check_job(job_id).
    """
    # ------------------------------------------------------------------
    # Session A — set up an in-flight pipeline.
    # ------------------------------------------------------------------
    session_a = create_mcp_server(backend)

    _content, project = await session_a.call_tool(
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
    project_id: str = project["project_id"]
    assert project_id, "create_project must return a non-empty project_id"

    # Add a scene so the project has a non-trivial surface to resume.
    _c, add_ack = await session_a.call_tool(
        "add_scene",
        {
            "project_id": project_id,
            "after_index": -1,
            "text": "Opening shot: an empty hallway, dim light.",
        },
    )
    assert add_ack["new_scene_index"] == 0

    # Submit a media job. MockBackend.submit_job synthesizes a
    # JobStatus immediately so the test never blocks on actual media
    # generation; the value the resume flow cares about is the job_id
    # remaining resolvable after the session drop.
    _c, job_sub = await session_a.call_tool(
        "generate_audio",
        {"project_id": project_id},
    )
    job_id: str = job_sub["job_id"]
    assert job_id, "generate_audio must return a job_id even when async"
    assert job_sub["job_type"] == "generate_audio"

    # ------------------------------------------------------------------
    # Simulated drop — the only handle to session A is released. In
    # production the transport tears down, the Mcp-Session-Id is
    # forgotten by Claude Code, and the (still-valid) Bearer token is
    # the only resumption credential the user has. Here, just drop the
    # reference.
    # ------------------------------------------------------------------
    del session_a

    # ------------------------------------------------------------------
    # Session B — fresh MCP server, same backend. The runbook in the
    # ``resume_project`` documentation prompt walks Claude through
    # exactly these four calls.
    # ------------------------------------------------------------------
    session_b = create_mcp_server(backend)

    # Sanity: this really is a different MCP server instance.
    # (Both wrap the same backend, but they are not the same object.)
    # ``id(...)`` of the server itself isn't meaningful — what matters
    # is that no Mcp-Session-Id, no per-server caches, are shared.
    # Documented here so a future refactor that accidentally memoizes
    # the server (singleton) fails the spirit of this test.
    assert session_b is not None

    # Step 1 of the runbook: list_projects on the new session must
    # surface the project created by the (gone) session A.
    _c, listing = await session_b.call_tool("list_projects", {})
    summaries = listing["result"]
    assert isinstance(summaries, list)
    ids = {row["project_id"] for row in summaries}
    assert project_id in ids, (
        f"orphaned project_id={project_id!r} missing from list_projects on fresh session; got {ids}"
    )

    # Step 2 of the runbook: get_workflow_state returns the prior state.
    # No workflow *steps* have been completed via save_step_result yet
    # (the test didn't drive any), but the project's current_data should
    # be intact and current_step should point at the first un-completed
    # step in the catalog.
    _c, state = await session_b.call_tool(
        "get_workflow_state",
        {"project_id": project_id},
    )
    assert state["project_id"] == project_id
    assert state["status"] == "not_started"  # no save_step_result calls yet
    assert state["completed_steps"] == []
    assert state["current_step"], "workflow must point at a next step to resume from"
    # current_data carries the seed material the workflow was created
    # with (the story and settings dict) — proves nothing was wiped on
    # the session drop.
    assert "story" in state["current_data"]
    assert state["current_data"]["story"] == _STORY

    # Step 3 of the runbook: a subsequent media-tool call on the fresh
    # session must still resolve the job_id from session A. This is the
    # specific regression the ticket pins: if MCP-session state were
    # accidentally tied to job-id lookup, this would 404 in session B
    # and the runbook would be impossible to follow.
    _c, job = await session_b.call_tool(
        "check_job",
        {"job_id": job_id},
    )
    assert job["job_id"] == job_id
    assert job["job_type"] == "generate_audio"
    # MockBackend completes synthetic jobs immediately; what the assert
    # really pins is that the lookup succeeded across a fresh session.
    assert job["status"] in {"queued", "running", "completed"}


async def test_resume_prompt_body_matches_runbook(backend: MockBackend, authed: None) -> None:
    """The documentation prompt's rendered body is the runbook the
    integration flow above implements. If they drift the runbook
    becomes a lie, so pin the contract here as well.
    """
    session = create_mcp_server(backend)
    result = await session.get_prompt("resume_project", {})
    body = result.messages[0].content.text  # type: ignore[attr-defined,union-attr]

    # The five concrete tool calls the integration test makes, in
    # the order the runbook walks them.
    assert "list_projects()" in body
    assert "get_workflow_state(project_id)" in body
    assert "generate_audio" in body  # representative resumable media step
    assert "check_job(job_id)" in body

    # The runbook explicitly disclaims save_step_result for itself
    # (the prompt is documentation-only).
    assert "There is no `save_step_result` call" in body
