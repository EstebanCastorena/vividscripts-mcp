"""Sec-E / KAN-98 — ``--seed-session`` safety hardening (audit finding #15).

The Phase-1 dev-seed shortcut mints a live session cookie for any
attacker-chosen ``user_id`` and prints it to stdout — CI logs, shell
scrollback, screen-share recordings. The audit listed three concrete
hardenings:

1. **Refuse non-loopback bind** unless ``VIVIDSCRIPTS_ALLOW_DEV_SEED=1``
   is set. The KAN-96 startup guard already gates non-loopback offline
   binds in general, but ``--seed-session`` mints a credential without
   any authentication — the additional opt-in keeps the flag from
   silently bootstrapping a back door when ``ALLOW_OFFLINE_NETWORK=1``
   has been turned on for some other legitimate reason.
2. **Validate ``user_id`` format.** A future caller that interpolates
   the value into a path / log line / filename inherits whatever shape
   the operator pasted — bound the alphabet at the CLI surface.
3. **Don't leak the cookie via stdout.** Write the seed material to
   stderr with a loud warning, or to a ``0600`` temp file path. Stdout
   ends up in CI logs and screen recordings; stderr is at least
   conventionally human-only and a temp file pins ownership.

The KAN-96 ``--seed-session`` tests assert the *startup guard* fires;
this file asserts the additional ``--seed-session`` hardenings.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

import pytest

from vividscripts_mcp.oauth.startup_guards import (
    ALLOW_OFFLINE_AUTH_ENV,
    ALLOW_OFFLINE_NETWORK_ENV,
)

ALLOW_DEV_SEED_ENV = "VIVIDSCRIPTS_ALLOW_DEV_SEED"


@contextmanager
def _env(**overrides: str | None) -> Iterator[None]:
    sentinel = object()
    saved: dict[str, Any] = {k: os.environ.get(k, sentinel) for k in overrides}
    try:
        for k, v in overrides.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is sentinel:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v  # type: ignore[assignment]


def _run_main(argv: list[str]) -> int:
    """Invoke the CLI ``main`` with uvicorn stubbed so nothing binds."""
    from vividscripts_mcp.__main__ import main

    with patch("uvicorn.run"):
        return main(argv)


# ---------------------------------------------------------------------
# Cookie must not appear on stdout
# ---------------------------------------------------------------------


class TestCookieNotOnStdout:
    """The vs_session cookie value must not reach stdout.

    Audit's principal concern — stdout is captured by every CI runner,
    every screen recorder, every paste-into-Slack.
    """

    def test_loopback_seed_does_not_print_cookie_value_to_stdout(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with _env(
            **{
                ALLOW_OFFLINE_AUTH_ENV: "1",
                ALLOW_OFFLINE_NETWORK_ENV: None,
                ALLOW_DEV_SEED_ENV: "1",
            }
        ):
            rc = _run_main(["serve", "--host", "127.0.0.1", "--seed-session", "user-alpha"])
        out = capsys.readouterr()
        assert rc == 0
        # The actual session id is the unsafe material — it is what an
        # attacker would replay. Pin the pattern, not the literal value.
        assert "vs_session=" not in out.out, (
            "the vs_session cookie value must not be written to stdout (audit finding #15)"
        )

    def test_loopback_seed_warns_via_stderr_or_redirected_sink(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The operator still needs the seed material — stderr is acceptable.

        The fix may instead point stdout at a 0600 temp file path; that
        also passes (and is even safer). Either way, *something* not
        going to stdout must convey the seed back to the caller.
        """
        with _env(
            **{
                ALLOW_OFFLINE_AUTH_ENV: "1",
                ALLOW_OFFLINE_NETWORK_ENV: None,
                ALLOW_DEV_SEED_ENV: "1",
            }
        ):
            rc = _run_main(["serve", "--host", "127.0.0.1", "--seed-session", "user-alpha"])
        out = capsys.readouterr()
        assert rc == 0
        # Either the cookie is in stderr, or stdout points the operator
        # at a temp-file path that holds it. Both are acceptable; both
        # exclude stdout from the leak surface.
        cookie_in_stderr = "vs_session=" in out.err
        temp_file_pointed_at = bool(re.search(r"(/tmp/|temp|file)", out.out, re.IGNORECASE))
        assert cookie_in_stderr or temp_file_pointed_at, (
            "operator must be able to retrieve the cookie from stderr or a temp file path"
        )


# ---------------------------------------------------------------------
# Non-loopback bind requires the dedicated dev-seed env flag
# ---------------------------------------------------------------------


class TestNonLoopbackRequiresDevSeedFlag:
    """``--seed-session`` on a public bind needs an explicit second opt-in.

    ``ALLOW_OFFLINE_AUTH`` + ``ALLOW_OFFLINE_NETWORK`` already allow a
    non-loopback offline server (for legitimate testing). But minting an
    auth-free session on a network-reachable host is its own escalation
    — the second flag keeps that capability separate.
    """

    def test_non_loopback_seed_refuses_without_dev_seed_env(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with _env(
            **{
                ALLOW_OFFLINE_AUTH_ENV: "1",
                ALLOW_OFFLINE_NETWORK_ENV: "1",
                ALLOW_DEV_SEED_ENV: None,
            }
        ):
            rc = _run_main(["serve", "--host", "0.0.0.0", "--seed-session", "user-alpha"])
        out = capsys.readouterr()
        # Non-zero rc OR a refusal message — either is acceptable.
        # The cookie must NOT be printed in either case.
        assert "vs_session=" not in out.out
        assert rc != 0 or "refus" in out.err.lower() or "refus" in out.out.lower()

    def test_non_loopback_seed_allowed_with_explicit_dev_seed_env(self) -> None:
        with _env(
            **{
                ALLOW_OFFLINE_AUTH_ENV: "1",
                ALLOW_OFFLINE_NETWORK_ENV: "1",
                ALLOW_DEV_SEED_ENV: "1",
            }
        ):
            rc = _run_main(["serve", "--host", "0.0.0.0", "--seed-session", "user-alpha"])
        assert rc == 0

    @pytest.mark.parametrize("bogus", ["0", "true", "yes", "on", ""])
    def test_dev_seed_env_must_be_literal_one(
        self, bogus: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Strict ``"1"`` matching, same idiom as the KAN-96 flags."""
        with _env(
            **{
                ALLOW_OFFLINE_AUTH_ENV: "1",
                ALLOW_OFFLINE_NETWORK_ENV: "1",
                ALLOW_DEV_SEED_ENV: bogus,
            }
        ):
            rc = _run_main(["serve", "--host", "0.0.0.0", "--seed-session", "user-alpha"])
        out = capsys.readouterr()
        assert "vs_session=" not in out.out
        assert rc != 0 or "refus" in out.err.lower()


# ---------------------------------------------------------------------
# user_id format validation
# ---------------------------------------------------------------------


class TestUserIdValidation:
    """The CLI rejects garbage ``user_id`` shapes before minting anything.

    A loose ``user_id`` flows into the audit log, the project key, and
    (post-Phase-3) the backend's URL space. Bound the alphabet here.
    """

    @pytest.mark.parametrize(
        "bad_user_id",
        [
            "",  # empty
            "../etc/passwd",  # path traversal
            "user with spaces",
            "user\nnewline",  # log injection
            "user;rm -rf",  # shell metacharacters
            "user'or'1'='1",
            "x" * 200,  # oversize
            "user@example.com",  # @ not allowed
            "user/slash",
            "<script>",
        ],
    )
    def test_bad_user_id_rejected(
        self, bad_user_id: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with _env(
            **{
                ALLOW_OFFLINE_AUTH_ENV: "1",
                ALLOW_OFFLINE_NETWORK_ENV: None,
                ALLOW_DEV_SEED_ENV: "1",
            }
        ):
            rc = _run_main(["serve", "--host", "127.0.0.1", "--seed-session", bad_user_id])
        out = capsys.readouterr()
        assert "vs_session=" not in out.out
        assert rc != 0, f"user_id {bad_user_id!r} should be rejected"

    @pytest.mark.parametrize(
        "good_user_id",
        ["user-alpha", "user_beta", "user.gamma", "User123", "a", "x" * 64],
    )
    def test_good_user_id_accepted(self, good_user_id: str) -> None:
        with _env(
            **{
                ALLOW_OFFLINE_AUTH_ENV: "1",
                ALLOW_OFFLINE_NETWORK_ENV: None,
                ALLOW_DEV_SEED_ENV: "1",
            }
        ):
            rc = _run_main(["serve", "--host", "127.0.0.1", "--seed-session", good_user_id])
        assert rc == 0
