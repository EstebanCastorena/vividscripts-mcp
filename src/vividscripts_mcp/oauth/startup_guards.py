"""Refuse-to-start guard for the offline OAuth path (KAN-96 / audit findings #5 + #6).

The audit flagged two compounding ways the package can boot in a
production-shaped configuration with development-grade auth still
wired in:

* **Finding #5** — ``/_mock_idp/login`` accepts an arbitrary ``user_id``
  with no authentication and mints a session+code for that user. The
  only barrier between this dev-only endpoint and production is the
  module docstring's "must never be enabled" — there is no runtime
  guard.

* **Finding #6** — the offline path (mock IdP route mount + in-process
  RSA self-mint signer) is selected purely by ``cognito is None``. A
  misconfigured / missing Cognito env therefore boots the server
  fully-functional with a process-local key minting its own RS256
  tokens. No fail-loud.

This module is the chokepoint. ``build_app(..., host=<bound host>)``
calls :func:`ensure_offline_path_allowed` when ``cognito`` is unset; the
function either returns (and emits a loud ``WARNING`` log so the boot is
visible in CloudWatch) or raises :class:`InsecureStartupRefused`.

Design constraints
------------------

* **The guard fires only when ``host`` is provided.** The 350+
  in-process tests that call ``build_app()`` without a host argument
  are not the threat model — ``TestClient`` does not bind a socket. The
  guard's concern is exactly "binding a network socket in offline
  mode", which only happens at the ``__main__.py`` entrypoint.

* **Strict ``"1"`` matching on both env flags.** ``"true"``, ``"yes"``,
  ``"on"`` do *not* opt in. This avoids the classic "I set it to false,
  you took it as truthy" footgun and gives the operator exactly one
  shape to grep for in deploy logs.

* **Two flags, not one.** ``VIVIDSCRIPTS_ALLOW_OFFLINE_AUTH=1`` opts in
  to running the offline auth path *at all*; that alone is not enough
  to bind a non-loopback host. ``VIVIDSCRIPTS_ALLOW_OFFLINE_NETWORK=1``
  is the second key that authorizes a public bind. Two flags means a
  single shell typo cannot expose the mock IdP to the network.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping

#: Env flag the operator must set to ``"1"`` to allow the offline path
#: to boot at all. Anything else (including unset) refuses to start.
ALLOW_OFFLINE_AUTH_ENV = "VIVIDSCRIPTS_ALLOW_OFFLINE_AUTH"

#: Env flag that additionally authorizes a non-loopback bind in offline
#: mode. Without this, the offline path is loopback-only even with
#: :data:`ALLOW_OFFLINE_AUTH_ENV` set.
ALLOW_OFFLINE_NETWORK_ENV = "VIVIDSCRIPTS_ALLOW_OFFLINE_NETWORK"

#: Hosts that resolve to the loopback interface. The IPv6 bracketed
#: form is accepted because uvicorn's ``--host`` allows it and it would
#: otherwise be rejected as "non-loopback".
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "[::1]", "localhost"})

logger = logging.getLogger(__name__)


class InsecureStartupRefused(RuntimeError):
    """Raised when the offline OAuth path would boot without explicit opt-in.

    The exception message names the env flag(s) the operator must set
    to proceed — refusing without telling the operator *how* to override
    just produces frustrated `--force` patches.
    """


def _is_loopback_host(host: str) -> bool:
    """Return ``True`` if ``host`` is a recognized loopback spelling."""
    return host.strip().lower() in _LOOPBACK_HOSTS


def _flag_is_opt_in(value: str | None) -> bool:
    """Strict ``"1"`` opt-in. Anything else — including unset — is no."""
    return value == "1"


def ensure_offline_path_allowed(
    host: str,
    *,
    env: Mapping[str, str] | None = None,
) -> None:
    """Refuse to boot the offline auth path unless explicitly opted in.

    Call sites pass the bind host that ``uvicorn`` will listen on. The
    function either returns (after emitting a loud ``WARNING`` log so
    the offline boot is visible in centralized logs) or raises
    :class:`InsecureStartupRefused` with a message naming the env flag
    the operator must set.

    ``env`` defaults to :data:`os.environ` and is parameterized only to
    keep the function pure-testable.
    """
    source = env if env is not None else os.environ

    auth_flag = _flag_is_opt_in(source.get(ALLOW_OFFLINE_AUTH_ENV))
    network_flag = _flag_is_opt_in(source.get(ALLOW_OFFLINE_NETWORK_ENV))
    loopback = _is_loopback_host(host)

    if not auth_flag:
        raise InsecureStartupRefused(
            "Refusing to start: the offline OAuth path (mock IdP + in-process "
            "self-mint signer) is selected (no Cognito configured), but the "
            f"explicit opt-in env flag {ALLOW_OFFLINE_AUTH_ENV}=1 is not set. "
            "If you are running a dev server on loopback, export "
            f"{ALLOW_OFFLINE_AUTH_ENV}=1 in your shell. If you intended to run "
            "in production, configure Cognito via CognitoConfig — see "
            "docs/auth.md."
        )

    if not loopback and not network_flag:
        raise InsecureStartupRefused(
            f"Refusing to start: bound host {host!r} is not loopback. The "
            "offline OAuth path is loopback-only by default. To bind a "
            f"non-loopback interface in offline mode, also set "
            f"{ALLOW_OFFLINE_NETWORK_ENV}=1 — and understand that you are "
            "exposing the mock IdP (any user_id is accepted with no password) "
            "and a process-local RS256 signer to the network."
        )

    logger.warning(
        "Offline OAuth path active: mock IdP route is mounted and the "
        "in-process self-mint RSA signer is signing access tokens. "
        "DO NOT expose this configuration to production — it is intended "
        "for local development only. Configure Cognito via CognitoConfig "
        "to disable this path."
    )

    if not loopback:
        logger.warning(
            "Offline OAuth path bound to non-loopback host %r "
            "(%s=1 set). The mock IdP /_mock_idp/login endpoint accepts "
            "any user_id with no authentication and is now reachable over "
            "the network. This is a development-only configuration.",
            host,
            ALLOW_OFFLINE_NETWORK_ENV,
        )
