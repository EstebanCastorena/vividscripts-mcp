"""Tests for the 20 PromptInterface declarations (KAN-56)."""

from __future__ import annotations

import re
from collections import defaultdict, deque

import jsonschema
import pytest
from pydantic import ValidationError

from vividscripts_mcp.prompts import PROMPT_INTERFACES, PromptInterface

# The canonical 19 — Phase 2 scope locked 2026-05-14, including the
# thumbnail_format_selector addition from slide_editor commit 8ae047d.
# Dropped 2026-05-25: motion_direction (Kling animation routed out of the
# default MCP pipeline; see Test 2 post-mortem in Obsidian).
EXPECTED_NAMES = frozenset(
    {
        "story_blueprint",
        "narration_grouping",
        "story_summarizer",
        "title_generator",
        "short_title_generator",
        "stage_direction_bible",
        "stage_direction_first",
        "stage_direction_subsequent",
        "image_split_analyzer",
        "image_director_first",
        "image_director_subsequent",
        "image_director_followup",
        "sound_effect_category",
        "sound_effect_analyzer",
        "thumbnail",
        "thumbnail_text",
        "thumbnail_format_selector",
        "story_optimization",
        "image_prompt_edit",
    }
)


# ---------------------------------------------------------------------------
# Catalog invariants
# ---------------------------------------------------------------------------


def test_exactly_19_interfaces() -> None:
    """The Phase 2 scope is exactly 19 prompts after the 2026-05-25 drop of
    motion_direction (see [[Projects/VividScripts/Post-Mortems/2026-05-25
    MCP Story-to-Video Test 2]]). Neither more nor less."""
    assert len(PROMPT_INTERFACES) == 19


def test_keys_match_names() -> None:
    """Dict key must equal interface.name (catches copy-paste typos)."""
    for key, interface in PROMPT_INTERFACES.items():
        assert key == interface.name


def test_all_expected_names_present() -> None:
    assert set(PROMPT_INTERFACES.keys()) == EXPECTED_NAMES


def test_names_are_snake_case() -> None:
    pattern = re.compile(r"^[a-z][a-z0-9_]*$")
    for interface in PROMPT_INTERFACES.values():
        assert pattern.fullmatch(interface.name), f"name not snake_case: {interface.name!r}"


def test_names_are_unique() -> None:
    names = [i.name for i in PROMPT_INTERFACES.values()]
    assert len(names) == len(set(names))


# ---------------------------------------------------------------------------
# IP-leakage guards (per Phase 0 design refinement — bodies stay private)
# ---------------------------------------------------------------------------


def test_descriptions_do_not_leak_template_bodies() -> None:
    """Descriptions document agent role; they must NOT quote real templates.

    The smoke test: real templates start with phrases like 'You are a creative
    director' or 'TASK\\n'. Any description that opens that way is leaking the
    body. Per [[MCP Phase 0 Notes]] the bodies are creative IP and stay in
    slide_editor.
    """
    forbidden_openings = ("You are", "TASK\n", "TASK:", "Your task")
    for interface in PROMPT_INTERFACES.values():
        for opening in forbidden_openings:
            assert opening not in interface.description, (
                f"{interface.name}: description appears to quote the template "
                f"body (contains {opening!r})"
            )


def test_descriptions_are_substantive() -> None:
    """Each description should be a real paragraph, not a placeholder."""
    for interface in PROMPT_INTERFACES.values():
        # At least 100 chars — a one-paragraph agent description
        assert len(interface.description) >= 100, (
            f"{interface.name}: description too short ({len(interface.description)} chars)"
        )


# ---------------------------------------------------------------------------
# Schema invariants
# ---------------------------------------------------------------------------


def test_output_schema_refs_point_at_schemas_dir() -> None:
    """output_schema_ref must be <name>.json (resolved by schemas/ in KAN-57)."""
    for interface in PROMPT_INTERFACES.values():
        assert interface.output_schema_ref == f"{interface.name}.json", (
            f"{interface.name}: output_schema_ref must equal '<name>.json', "
            f"got {interface.output_schema_ref!r}"
        )


def test_input_schemas_are_valid_json_schema_draft_2020_12() -> None:
    """Every input_schema must validate as JSON Schema Draft 2020-12."""
    for interface in PROMPT_INTERFACES.values():
        # Raises SchemaError if invalid
        jsonschema.Draft202012Validator.check_schema(interface.input_schema)


def test_input_schemas_are_objects() -> None:
    """All input schemas describe an object (the context dict shape)."""
    for interface in PROMPT_INTERFACES.values():
        assert interface.input_schema.get("type") == "object", (
            f"{interface.name}: input_schema.type must be 'object'"
        )


def test_input_schemas_forbid_additional_properties() -> None:
    """Strict input contracts: unknown context fields fail loudly."""
    for interface in PROMPT_INTERFACES.values():
        assert interface.input_schema.get("additionalProperties") is False, (
            f"{interface.name}: input_schema must forbid additionalProperties"
        )


def test_input_schemas_have_at_least_one_property() -> None:
    """Every prompt takes some input — no zero-arg prompts in Phase 2."""
    for interface in PROMPT_INTERFACES.values():
        properties = interface.input_schema.get("properties", {})
        assert len(properties) >= 1, f"{interface.name}: input_schema has no properties"


# ---------------------------------------------------------------------------
# loops_over invariants
# ---------------------------------------------------------------------------


ALLOWED_LOOPS_OVER = frozenset({None, "story", "paragraph", "scene", "segment"})


def test_loops_over_in_allowed_set() -> None:
    for interface in PROMPT_INTERFACES.values():
        assert interface.loops_over in ALLOWED_LOOPS_OVER, (
            f"{interface.name}: loops_over={interface.loops_over!r} not in {ALLOWED_LOOPS_OVER}"
        )


# ---------------------------------------------------------------------------
# Dependency graph (depends_on) invariants
# ---------------------------------------------------------------------------


def test_depends_on_references_are_valid() -> None:
    """Every depends_on entry must be a known prompt name."""
    all_names = set(PROMPT_INTERFACES.keys())
    for interface in PROMPT_INTERFACES.values():
        for dep in interface.depends_on:
            assert dep in all_names, (
                f"{interface.name}: depends_on references unknown prompt {dep!r}"
            )


def test_no_self_dependency() -> None:
    for interface in PROMPT_INTERFACES.values():
        assert interface.name not in interface.depends_on, (
            f"{interface.name}: cannot depend on itself"
        )


def test_depends_on_graph_is_acyclic() -> None:
    """Khan's topological sort — visiting all nodes means no cycle."""
    graph: dict[str, list[str]] = defaultdict(list)
    in_degree: dict[str, int] = {name: 0 for name in PROMPT_INTERFACES}
    for name, interface in PROMPT_INTERFACES.items():
        for dep in interface.depends_on:
            graph[dep].append(name)
            in_degree[name] += 1

    queue: deque[str] = deque(n for n, d in in_degree.items() if d == 0)
    visited = 0
    while queue:
        node = queue.popleft()
        visited += 1
        for child in graph[node]:
            in_degree[child] -= 1
            if in_degree[child] == 0:
                queue.append(child)

    assert visited == len(PROMPT_INTERFACES), (
        f"cycle detected in depends_on graph (visited {visited}/{len(PROMPT_INTERFACES)})"
    )


def test_root_prompts_match_expectations() -> None:
    """Sanity check: the only prompts without dependencies are the pipeline
    head (story_blueprint) and the two user-initiated tabs."""
    roots = {name for name, i in PROMPT_INTERFACES.items() if not i.depends_on}
    assert roots == {
        "story_blueprint",
        "story_optimization",
        "image_prompt_edit",
    }, f"unexpected root prompts: {roots}"


# ---------------------------------------------------------------------------
# PromptInterface model invariants
# ---------------------------------------------------------------------------


def test_interface_is_frozen() -> None:
    """Interfaces are immutable — mutation should raise."""
    interface = next(iter(PROMPT_INTERFACES.values()))
    with pytest.raises(ValidationError):
        interface.name = "tampered"  # type: ignore[misc]


def test_extra_fields_forbidden() -> None:
    """The model rejects unknown fields (catches drift)."""
    with pytest.raises(ValidationError):
        PromptInterface(
            name="test",
            description="x" * 120,
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            output_schema_ref="test.json",
            loops_over=None,
            depends_on=(),
            spurious_field="oops",  # type: ignore[call-arg]
        )
