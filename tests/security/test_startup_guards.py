"""Sec-C / KAN-96 — refuse-to-start guard for the offline OAuth path.

Pins the boot-time contract that keeps the mock IdP route and the
in-process self-mint RSA signer from silently shipping in a
production-shaped configuration (the audit's findings #5 + #6).

The contract is intentionally narrow:

* The guard fires only when ``build_app(..., host=<bound host>)`` is
  invoked with ``cognito=None``. That captures the production entry
  point (``__main__.py:serve`` → ``uvicorn.run``) and any future caller
  that explicitly binds a socket. The 350+ in-process tests that call
  ``build_app()`` without a ``host`` argument continue to work
  unchanged — they are not the threat model.
* The two env flags use strict ``"1"`` matching, not generic truthy
  parsing. ``ALLOW_OFFLINE_AUTH=true`` does not opt in. This avoids the
  classic "I set it to false, you took it as truthy" footgun and keeps
  the audit trail clean.
* Refusal is a hard raise (``InsecureStartupRefused``); the audit
  language is "must refuse to start". Tests assert on the exception
  type and the warning log record, not on stderr.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from vividscripts_mcp.oauth.cognito import CognitoConfig
from vividscripts_mcp.oauth.startup_guards import (
    ALLOW_OFFLINE_AUTH_ENV,
    ALLOW_OFFLINE_NETWORK_ENV,
    InsecureStartupRefused,
    ensure_offline_path_allowed,
)
from vividscripts_mcp.server import build_app

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


@contextmanager
def _env(**overrides: str | None) -> Iterator[None]:
    """Temporarily mutate ``os.environ`` for a test block.

    A value of ``None`` deletes the variable. Cleaner than
    ``monkeypatch.setenv`` when we want one tight `with` block.
    """
    import os

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


def _broker_cognito() -> CognitoConfig:
    """A minimal CognitoConfig — broker mode bypasses the offline guard."""
    return CognitoConfig(
        issuer="https://cognito-idp.us-east-1.amazonaws.com/us-east-1_TEST",
        client_id="cognito-app-client-id",
        client_secret="super-secret",
        hosted_ui_domain="https://auth.vividscripts.ai/",
        public_base_url="https://vividscripts.ai/",
    )


# ---------------------------------------------------------------------
# A. ensure_offline_path_allowed — pure-function contract
# ---------------------------------------------------------------------


class TestEnsureOfflinePathAllowed:
    """Pure-function tests on the guard, independent of build_app."""

    def test_loopback_without_opt_in_refuses(self) -> None:
        with (
            _env(**{ALLOW_OFFLINE_AUTH_ENV: None, ALLOW_OFFLINE_NETWORK_ENV: None}),
            pytest.raises(InsecureStartupRefused) as exc,
        ):
            ensure_offline_path_allowed("127.0.0.1")
        # The error string must name the env flag the operator has to set —
        # otherwise the message is useless at 3am.
        assert ALLOW_OFFLINE_AUTH_ENV in str(exc.value)

    def test_loopback_with_opt_in_succeeds_and_warns(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING, logger="vividscripts_mcp.oauth.startup_guards")
        with _env(**{ALLOW_OFFLINE_AUTH_ENV: "1", ALLOW_OFFLINE_NETWORK_ENV: None}):
            ensure_offline_path_allowed("127.0.0.1")
        # At least one WARNING-or-higher record must mention the offline path —
        # operators rely on this to spot a misconfigured prod deploy in logs.
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings, "expected at least one WARNING+ record on offline boot"
        assert any("offline" in r.getMessage().lower() for r in warnings)

    def test_non_loopback_with_opt_in_only_still_refuses(self) -> None:
        """The single ``ALLOW_OFFLINE_AUTH`` flag is not enough for a public bind.

        The audit's worst case is offline auth on a network-reachable
        host. Allowing that with one flag would defeat the second layer.
        """
        with (
            _env(**{ALLOW_OFFLINE_AUTH_ENV: "1", ALLOW_OFFLINE_NETWORK_ENV: None}),
            pytest.raises(InsecureStartupRefused) as exc,
        ):
            ensure_offline_path_allowed("0.0.0.0")
        assert ALLOW_OFFLINE_NETWORK_ENV in str(exc.value)

    def test_non_loopback_with_both_flags_succeeds_and_warns_louder(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING, logger="vividscripts_mcp.oauth.startup_guards")
        with _env(**{ALLOW_OFFLINE_AUTH_ENV: "1", ALLOW_OFFLINE_NETWORK_ENV: "1"}):
            ensure_offline_path_allowed("0.0.0.0")
        # Both warnings should fire: the base "offline path active" line
        # AND a second one specifically about the non-loopback bind.
        messages = [r.getMessage().lower() for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("offline" in m for m in messages)
        assert any("non-loopback" in m or "network" in m for m in messages)

    @pytest.mark.parametrize("host", ["127.0.0.1", "::1", "[::1]", "localhost"])
    def test_loopback_variants_accepted_with_opt_in(self, host: str) -> None:
        """All canonical loopback spellings count as loopback."""
        with _env(**{ALLOW_OFFLINE_AUTH_ENV: "1", ALLOW_OFFLINE_NETWORK_ENV: None}):
            ensure_offline_path_allowed(host)

    @pytest.mark.parametrize(
        "host",
        ["0.0.0.0", "192.168.1.10", "10.0.0.1", "example.com", "vividscripts.ai", "::"],
    )
    def test_non_loopback_variants_require_network_flag(self, host: str) -> None:
        """Any non-loopback bind must require the explicit network flag.

        Includes ``0.0.0.0`` and the IPv6 unspecified ``::``, both of
        which bind every interface and are the most common foot-shooting
        configuration.
        """
        with (
            _env(**{ALLOW_OFFLINE_AUTH_ENV: "1", ALLOW_OFFLINE_NETWORK_ENV: None}),
            pytest.raises(InsecureStartupRefused),
        ):
            ensure_offline_path_allowed(host)

    @pytest.mark.parametrize("bogus", ["0", "true", "TRUE", "yes", "Y", "on", ""])
    def test_opt_in_value_must_be_literal_one(self, bogus: str) -> None:
        """Strict ``"1"`` matching. ``true``/``yes``/``on`` do not opt in.

        Defeats the "I set it to false but you read it as truthy" footgun
        and gives operators exactly one shape to grep for in deploy logs.
        """
        with (
            _env(**{ALLOW_OFFLINE_AUTH_ENV: bogus, ALLOW_OFFLINE_NETWORK_ENV: None}),
            pytest.raises(InsecureStartupRefused),
        ):
            ensure_offline_path_allowed("127.0.0.1")

    def test_warning_message_calls_out_production_risk(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The warning has to be loud enough to spot in CloudWatch.

        "DO NOT" + an explicit mention of production or mock IdP is the
        minimum bar — operators scan for that pattern.
        """
        caplog.set_level(logging.WARNING, logger="vividscripts_mcp.oauth.startup_guards")
        with _env(**{ALLOW_OFFLINE_AUTH_ENV: "1", ALLOW_OFFLINE_NETWORK_ENV: None}):
            ensure_offline_path_allowed("127.0.0.1")
        joined = "\n".join(
            r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING
        ).lower()
        assert "do not" in joined or "do-not" in joined or "never" in joined
        assert "mock" in joined or "production" in joined or "self-mint" in joined


# ---------------------------------------------------------------------
# B. build_app integration — the guard fires from the boot path
# ---------------------------------------------------------------------


class TestBuildAppIntegration:
    """The guard must run *inside* build_app when host is bound."""

    def test_build_app_cognito_none_non_loopback_refuses(self) -> None:
        with (
            _env(**{ALLOW_OFFLINE_AUTH_ENV: None, ALLOW_OFFLINE_NETWORK_ENV: None}),
            pytest.raises(InsecureStartupRefused),
        ):
            build_app(host="0.0.0.0")

    def test_build_app_cognito_none_loopback_without_env_refuses(self) -> None:
        with (
            _env(**{ALLOW_OFFLINE_AUTH_ENV: None, ALLOW_OFFLINE_NETWORK_ENV: None}),
            pytest.raises(InsecureStartupRefused),
        ):
            build_app(host="127.0.0.1")

    def test_build_app_cognito_none_loopback_with_env_succeeds(self) -> None:
        with _env(**{ALLOW_OFFLINE_AUTH_ENV: "1", ALLOW_OFFLINE_NETWORK_ENV: None}):
            app = build_app(host="127.0.0.1")
        assert app is not None

    def test_build_app_in_process_no_host_preserves_existing_behavior(self) -> None:
        """When no ``host`` is given, the call is library/test usage.

        The guard's threat model is "binding a network socket in
        offline mode"; an in-process TestClient does not bind. Existing
        suites use ``build_app()`` with no host — they must keep working
        without ANY env-flag scaffolding.
        """
        with _env(**{ALLOW_OFFLINE_AUTH_ENV: None, ALLOW_OFFLINE_NETWORK_ENV: None}):
            app = build_app()
        assert app is not None

    def test_build_app_with_cognito_skips_guard(self) -> None:
        """Broker mode is itself the trust signal — guard does not fire."""
        with _env(**{ALLOW_OFFLINE_AUTH_ENV: None, ALLOW_OFFLINE_NETWORK_ENV: None}):
            app = build_app(host="0.0.0.0", cognito=_broker_cognito())
        assert app is not None


# ---------------------------------------------------------------------
# C. Mock-IdP route mounting is gated by the guard
# ---------------------------------------------------------------------


class TestMockIdpRouteMounting:
    """``/_mock_idp/login`` is only reachable when offline auth is allowed."""

    def test_mock_idp_route_unreachable_when_guard_blocks_boot(self) -> None:
        """If build_app refuses, the route is never mounted by definition.

        The route exists in the offline branch and only the offline
        branch — if we cannot reach the route through a successful
        build, the audit's #5 concern (production accidentally serving
        ``/_mock_idp/login``) is structurally impossible.
        """
        with (
            _env(**{ALLOW_OFFLINE_AUTH_ENV: None, ALLOW_OFFLINE_NETWORK_ENV: None}),
            pytest.raises(InsecureStartupRefused),
        ):
            build_app(host="127.0.0.1")

    def test_mock_idp_route_mounted_when_offline_opt_in_set(self) -> None:
        with (
            _env(**{ALLOW_OFFLINE_AUTH_ENV: "1", ALLOW_OFFLINE_NETWORK_ENV: None}),
            TestClient(build_app(host="127.0.0.1"), base_url="http://127.0.0.1:8000") as client,
        ):
            # GET serves the HTML login form; we don't follow through —
            # just confirm the path exists (not 404).
            response = client.get("/_mock_idp/login?request_id=anything")
        assert response.status_code != 404, (
            "/_mock_idp/login must be mounted in offline-opt-in mode"
        )

    def test_mock_idp_route_not_mounted_in_broker_mode(self) -> None:
        """Broker (production) mode must never expose the mock IdP."""
        with TestClient(
            build_app(cognito=_broker_cognito()), base_url="https://vividscripts.ai"
        ) as client:
            response = client.get("/_mock_idp/login?request_id=anything")
        assert response.status_code == 404


# ---------------------------------------------------------------------
# D. --seed-session CLI boot path is gated by the guard
# ---------------------------------------------------------------------


class TestSeedSessionCli:
    """The ``--seed-session`` shortcut must not bypass the boot-time guard.

    Audit finding #15 (Sec-D) covers the cookie-redaction part of this
    flag; this ticket only ensures the guard fires before uvicorn runs,
    regardless of which CLI flags were passed.
    """

    def test_seed_session_without_opt_in_refuses(self) -> None:
        from vividscripts_mcp.__main__ import main

        with (
            _env(**{ALLOW_OFFLINE_AUTH_ENV: None, ALLOW_OFFLINE_NETWORK_ENV: None}),
            patch("uvicorn.run") as uvicorn_run,
            pytest.raises(InsecureStartupRefused),
        ):
            main(["serve", "--host", "127.0.0.1", "--seed-session", "user-alpha"])
        uvicorn_run.assert_not_called()

    def test_seed_session_with_opt_in_boots(self) -> None:
        from vividscripts_mcp.__main__ import main

        with (
            _env(**{ALLOW_OFFLINE_AUTH_ENV: "1", ALLOW_OFFLINE_NETWORK_ENV: None}),
            patch("uvicorn.run") as uvicorn_run,
        ):
            rc = main(["serve", "--host", "127.0.0.1", "--seed-session", "user-alpha"])
        assert rc == 0
        uvicorn_run.assert_called_once()

    def test_plain_serve_without_opt_in_refuses(self) -> None:
        """No ``--seed-session`` either — the boot path is what's gated."""
        from vividscripts_mcp.__main__ import main

        with (
            _env(**{ALLOW_OFFLINE_AUTH_ENV: None, ALLOW_OFFLINE_NETWORK_ENV: None}),
            patch("uvicorn.run") as uvicorn_run,
            pytest.raises(InsecureStartupRefused),
        ):
            main(["serve", "--host", "127.0.0.1"])
        uvicorn_run.assert_not_called()
