"""CLI entrypoint for the ``vividscripts-mcp`` console script.

Usage::

    vividscripts-mcp serve --port 8000 --backend mock

Phase 1 supports a single subcommand (``serve``) and a single backend (mock).
The ``--backend`` flag is reserved for KAN-31 (Phase 3), when the real
VividScripts backend adapter lands.

The ``--seed-session`` flag pre-creates a mock session at boot so you can
exercise the OAuth flow (which requires a session-cookie for Dynamic
Client Registration) without first walking the mock-IdP login. Useful
for manual end-to-end testing while Phase 1 has no real web app to log in
to. The flag prints the cookie value to stdout so you can paste it into
curl.
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
    serve.add_argument(
        "--seed-session",
        metavar="USER_ID",
        default=None,
        help=(
            "Pre-create a mock session for the given user id and print "
            "the vs_session cookie value. Phase-1 dev shortcut; not for "
            "production."
        ),
    )

    args = parser.parse_args(argv)

    if args.command == "serve":
        import uvicorn

        from vividscripts_mcp.oauth.session import (
            SESSION_COOKIE_NAME,
            MockSessionStore,
        )
        from vividscripts_mcp.server import build_app

        # Build the app first so the KAN-96 startup guard fires before
        # any --seed-session cookie is printed or any port is bound.
        # In offline mode (no Cognito), build_app raises
        # InsecureStartupRefused unless the operator has set
        # VIVIDSCRIPTS_ALLOW_OFFLINE_AUTH=1 (and, for a non-loopback
        # host, VIVIDSCRIPTS_ALLOW_OFFLINE_NETWORK=1).
        session_store = MockSessionStore()
        app = build_app(host=args.host, session_store=session_store)

        if args.seed_session is not None:
            info = session_store.create(user_id=args.seed_session)
            print(
                f"Seeded mock session for user {args.seed_session!r}.\n"
                f"  Cookie: {SESSION_COOKIE_NAME}={info.session_id}\n"
                "  Use it on /oauth/register, e.g.:\n"
                f"    curl -H 'Cookie: {SESSION_COOKIE_NAME}={info.session_id}' ...",
                flush=True,
            )

        uvicorn.run(app, host=args.host, port=args.port)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
