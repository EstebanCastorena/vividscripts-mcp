"""Guards for docs/prompts.md (KAN-61).

The doc is interface-only by policy — prompt bodies are private. These
tests enforce that in CI so a future hand-edit can't leak a template,
and that every prompt stays documented.
"""

from __future__ import annotations

from pathlib import Path

from vividscripts_mcp.prompts import PROMPT_INTERFACES

_DOC = Path(__file__).parents[2] / "docs" / "prompts.md"


def test_doc_exists() -> None:
    assert _DOC.is_file(), "docs/prompts.md is missing"


def test_no_template_body_leaked() -> None:
    """Real templates open with 'You are' / 'TASK'. None may appear here.

    This is the KAN-61 acceptance grep, enforced in CI.
    """
    text = _DOC.read_text(encoding="utf-8")
    for marker in ("You are", "TASK\n", "TASK:", "Your task"):
        assert marker not in text, (
            f"docs/prompts.md contains template-body marker {marker!r} — "
            f"prompt bodies must stay private"
        )


def test_every_prompt_documented() -> None:
    """Each of the 20 prompts has its own `### \\`<name>\\`` section."""
    text = _DOC.read_text(encoding="utf-8")
    for name in PROMPT_INTERFACES:
        assert f"### `{name}`" in text, f"{name} not documented in docs/prompts.md"


def test_doc_links_each_output_schema() -> None:
    text = _DOC.read_text(encoding="utf-8")
    for name in PROMPT_INTERFACES:
        assert f"schemas/{name}.json" in text, (
            f"docs/prompts.md does not link {name}'s output schema"
        )


def test_doc_covers_custom_overrides_and_shared_templates() -> None:
    text = _DOC.read_text(encoding="utf-8")
    assert "## Custom overrides" in text
    assert "## Shared templates" in text
    assert "image_director_followup" in text
