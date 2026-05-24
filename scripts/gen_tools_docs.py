"""Regenerate the auto-generated parameter blocks in ``docs/tools.md``.

The catalog file is a static, hand-edited Markdown document — the
"what is this for" prose and the realistic example calls stay
hand-written. This script only emits the *parameter* tables, which are
mechanically derivable from the FastMCP tool registry and the
``PROMPT_INTERFACES`` declarations.

Usage::

    python scripts/gen_tools_docs.py            # print to stdout
    python scripts/gen_tools_docs.py --check    # exit 1 if stale

The generated blocks are framed with HTML comment markers so they can
be re-spliced into the file without disturbing the prose around them::

    <!-- gen-tools:start name=create_project -->
    ...table...
    <!-- gen-tools:end -->

This is a developer convenience, not a build step. The shipped doc is
authoritative.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from vividscripts_mcp.adapters.mock import MockBackend
from vividscripts_mcp.prompts import PROMPT_INTERFACES
from vividscripts_mcp.server import create_mcp_server

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DOC = REPO_ROOT / "docs" / "tools.md"

BLOCK_RE = re.compile(
    r"<!-- gen-tools:start name=(?P<name>[a-z0-9_]+) -->\n.*?\n<!-- gen-tools:end -->",
    re.DOTALL,
)


def _type_label(spec: dict[str, Any]) -> str:
    """Turn a JSON-Schema fragment into a short human-readable type."""
    if "$ref" in spec:
        return spec["$ref"].rsplit("/", 1)[-1]
    if "anyOf" in spec:
        return " | ".join(_type_label(s) for s in spec["anyOf"])
    t = spec.get("type")
    if isinstance(t, list):
        return " | ".join(t)
    if t == "array":
        items = spec.get("items", {})
        return f"array<{_type_label(items)}>"
    return str(t or "any")


def _tool_param_table(schema: dict[str, Any]) -> str:
    """Render a tool's input schema as a parameters table."""
    props = schema.get("properties", {})
    if not props:
        return "_No parameters._"
    required = set(schema.get("required", []))
    lines = ["| Param | Type | Required | Description |", "|---|---|---|---|"]
    for name, spec in props.items():
        description = spec.get("description", "").replace("\n", " ").strip() or "—"
        mark = "yes" if name in required else "no"
        lines.append(f"| `{name}` | `{_type_label(spec)}` | {mark} | {description} |")
    return "\n".join(lines)


def _prompt_param_table(schema: dict[str, Any]) -> str:
    """Render a prompt input schema as a parameters table."""
    return _tool_param_table(schema)


async def _collect_tool_schemas() -> dict[str, dict[str, Any]]:
    mcp = create_mcp_server(MockBackend())
    tools = await mcp.list_tools()
    return {t.name: (t.inputSchema or {}) for t in tools}


def _render_blocks() -> dict[str, str]:
    """Return ``{block_name: markdown}`` for every auto-section in the doc."""
    tool_schemas = asyncio.run(_collect_tool_schemas())
    blocks: dict[str, str] = {}
    for name, schema in tool_schemas.items():
        blocks[name] = _tool_param_table(schema)
    for name, interface in PROMPT_INTERFACES.items():
        blocks[f"prompt_{name}"] = _prompt_param_table(interface.input_schema)
    return blocks


def _splice(doc: str, blocks: dict[str, str]) -> str:
    """Replace every ``<!-- gen-tools:start name=... -->...end -->`` block."""

    def _sub(match: re.Match[str]) -> str:
        name = match.group("name")
        body = blocks.get(name)
        if body is None:
            return match.group(0)
        return f"<!-- gen-tools:start name={name} -->\n{body}\n<!-- gen-tools:end -->"

    return BLOCK_RE.sub(_sub, doc)


def _missing_blocks(doc: str, blocks: Iterable[str]) -> list[str]:
    """Names with no corresponding marker pair in the doc."""
    present = {m.group("name") for m in BLOCK_RE.finditer(doc)}
    return [name for name in blocks if name not in present]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if the doc is out of date instead of rewriting it.",
    )
    parser.add_argument(
        "--print-json",
        action="store_true",
        help="Dump every tool's input schema as JSON for inspection.",
    )
    args = parser.parse_args(argv)

    if args.print_json:
        json.dump(asyncio.run(_collect_tool_schemas()), sys.stdout, indent=2)
        return 0

    blocks = _render_blocks()
    doc = TOOLS_DOC.read_text(encoding="utf-8")
    new_doc = _splice(doc, blocks)

    missing = _missing_blocks(new_doc, blocks)
    if missing:
        print(
            f"warning: no marker block for: {', '.join(sorted(missing))}",
            file=sys.stderr,
        )

    if args.check:
        if new_doc != doc:
            print("docs/tools.md is out of date — run scripts/gen_tools_docs.py", file=sys.stderr)
            return 1
        return 0

    TOOLS_DOC.write_text(new_doc, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
