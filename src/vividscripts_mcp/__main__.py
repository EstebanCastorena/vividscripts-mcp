"""CLI entrypoint for the ``vividscripts-mcp`` console script.

Usage::

    vividscripts-mcp serve --port 8000 --backend mock

Phase 1 supports a single subcommand (``serve``) and a single backend (mock).
The ``--backend`` flag is reserved for KAN-31 (Phase 3), when the real
VividScripts backend adapter lands.
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="vividscripts-mcp",
        description=(
            "VividScripts MCP server — remote MCP endpoint for AI-driven story-to-video workflows."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="Run the MCP server.")
    serve.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address (default: 127.0.0.1)",
    )
    serve.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Bind port (default: 8000)",
    )
    serve.add_argument(
        "--backend",
        choices=["mock"],
        default="mock",
        help="Backend implementation to use (default: mock)",
    )

    args = parser.parse_args(argv)

    if args.command == "serve":
        import uvicorn

        from vividscripts_mcp.server import build_app

        uvicorn.run(build_app(), host=args.host, port=args.port)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
