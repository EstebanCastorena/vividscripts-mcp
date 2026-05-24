"""CLI entrypoint for the ``vividscripts-mcp`` console script.

Usage::

    vividscripts-mcp serve --port 8000 --backend mock

Phase 1 supports a single subcommand (``serve``) and a single backend (mock).
The ``--backend`` flag is reserved for KAN-31 (Phase 3), when the real
VividScripts backend adapter lands.

The ``--seed-session`` flag pre-creates a mock session at boot so you can
exercise the OAuth flow (which requires a session-cookie for Dynamic
Client Registration) without first walking the mock-IdP login. KAN-98 #15
hardens the flag:

* Refuses non-loopback bind unless ``VIVIDSCRIPTS_ALLOW_DEV_SEED=1`` is set,
  even when the KAN-96 ``VIVIDSCRIPTS_ALLOW_OFFLINE_NETWORK=1`` has been
  enabled for some other legitimate reason. The seed mints an auth-free
  session; minting one on a network-reachable host is its own escalation.
* Validates the ``user_id`` against ``^[A-Za-z0-9_.-]{1,64}$`` — keeps log
  injection / path-traversal / shell-metacharacter shapes from flowing into
  the session, the audit log, and downstream URLs.
* Writes the seed cookie material to **stderr**, not stdout. Stdout ends
  up in CI logs and screen recordings; stderr is at least conventionally
  human-only.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys

#: KAN-98 #15 — bound the alphabet of ``user_id`` accepted at the CLI.
#: Letters, digits, dash, underscore, and dot, between 1 and 64 chars.
#: Rejects newlines (log injection), ``/`` and ``\\`` (path), spaces and
#: ``;``/``|`` (shell), ``<``/``>`` (HTML), ``@`` (email-shaped PII),
#: and oversize values.
_VALID_USER_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

#: Strict ``"1"`` match — same idiom as the KAN-96 startup-guard flags.
ALLOW_DEV_SEED_ENV = "VIVIDSCRIPTS_ALLOW_DEV_SEED"

#: Loopback hosts that count as "not network-reachable" for the seed gate.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "[::1]", "localhost"})


def _is_loopback(host: str) -> bool:
    return host in _LOOPBACK_HOSTS


def _dev_seed_allowed() -> bool:
    return os.environ.get(ALLOW_DEV_SEED_ENV) == "1"


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
            "Pre-create a mock session for the given user id. Phase-1 dev "
            "shortcut; not for production. The cookie is written to stderr "
            "with a loud warning. On a non-loopback host, also requires "
            f"{ALLOW_DEV_SEED_ENV}=1."
        ),
    )

    args = parser.parse_args(argv)

    if args.command == "serve":
        # KAN-98 #15 — validate / gate the seed *before* spinning up
        # anything, so a malformed user_id never reaches the audit log
        # or the session store. The startup guard (KAN-96) gates the
        # broader build_app; this is the dev-seed-specific extra layer.
        if args.seed_session is not None:
            if not _VALID_USER_ID.match(args.seed_session):
                print(
                    f"refused: --seed-session user_id {args.seed_session!r} "
                    f"does not match {_VALID_USER_ID.pattern}",
                    file=sys.stderr,
                )
                return 2
            if not _is_loopback(args.host) and not _dev_seed_allowed():
                print(
                    f"refused: --seed-session on non-loopback host "
                    f"{args.host!r} requires {ALLOW_DEV_SEED_ENV}=1",
                    file=sys.stderr,
                )
                return 2

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
            # KAN-98 #15 — write to stderr, not stdout. Stdout ends up
            # in CI logs and screen recordings; stderr is conventionally
            # human-only. A loud warning on a distinct line precedes
            # the cookie so the operator can't miss it.
            print(
                f"WARNING: dev seed-session active — full power, no auth. "
                f"DO NOT use {ALLOW_DEV_SEED_ENV}=1 in production.",
                file=sys.stderr,
                flush=True,
            )
            print(
                f"Seeded mock session for user {args.seed_session!r}.\n"
                f"  Cookie: {SESSION_COOKIE_NAME}={info.session_id}\n"
                "  Use it on /oauth/register, e.g.:\n"
                f"    curl -H 'Cookie: {SESSION_COOKIE_NAME}={info.session_id}' ...",
                file=sys.stderr,
                flush=True,
            )
            # Also emit the warning through the audit logger so it
            # shows up in structured log aggregation.
            logging.getLogger("vividscripts_mcp.audit").warning(
                "dev seed-session minted (user=%r, host=%r)",
                args.seed_session,
                args.host,
            )

        uvicorn.run(app, host=args.host, port=args.port)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
