"""KAN-83 — global DCR rate-limit ceiling (app-layer defense-in-depth)."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from vividscripts_mcp.oauth.ratelimit import GlobalRateLimiter
from vividscripts_mcp.server import build_app


class _FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_ctor_validates_args():
    with pytest.raises(ValueError):
        GlobalRateLimiter(limit=0)
    with pytest.raises(ValueError):
        GlobalRateLimiter(window_seconds=0)


def test_allows_up_to_limit_then_blocks():
    clock = _FakeClock()
    rl = GlobalRateLimiter(limit=3, window_seconds=300, clock=clock)
    assert rl.check() is None
    assert rl.check() is None
    assert rl.check() is None
    blocked = rl.check()
    assert isinstance(blocked, int) and blocked >= 1


def test_rejected_attempt_not_recorded_window_recovers_exactly():
    clock = _FakeClock()
    rl = GlobalRateLimiter(limit=2, window_seconds=100, clock=clock)
    assert rl.check() is None  # t=1000
    assert rl.check() is None  # t=1000
    assert rl.check() is not None  # blocked, NOT recorded
    # Advance just past the first event's window expiry.
    clock.t = 1000.0 + 100 + 1
    assert rl.check() is None  # both original events aged out -> allowed


def test_sliding_window_partial_expiry():
    clock = _FakeClock()
    rl = GlobalRateLimiter(limit=2, window_seconds=100, clock=clock)
    assert rl.check() is None  # event @1000
    clock.t = 1050.0
    assert rl.check() is None  # event @1050
    assert rl.check() is not None  # 2 in window -> blocked
    clock.t = 1101.0  # @1000 expired, @1050 still in window
    assert rl.check() is None  # one slot freed
    assert rl.check() is not None  # full again


def test_retry_after_is_seconds_until_oldest_expires():
    clock = _FakeClock()
    rl = GlobalRateLimiter(limit=1, window_seconds=300, clock=clock)
    assert rl.check() is None
    clock.t = 1000.0 + 100  # 100s elapsed; oldest expires in 200s
    retry = rl.check()
    assert retry is not None
    assert 195 <= retry <= 205


def test_endpoint_returns_429_with_retry_after_and_body():
    """Limiter is checked before the session gate (cheap flood
    rejection) and returns the RFC-shaped 429."""
    rl = GlobalRateLimiter(limit=2, window_seconds=300)
    with TestClient(build_app(dcr_rate_limiter=rl)) as client:
        # Default (offline) build is session-gated, so these 401 at the
        # session gate — but they pass the rate check (which is first).
        r1 = client.post("/oauth/register", json={"redirect_uris": ["https://a/cb"]})
        r2 = client.post("/oauth/register", json={"redirect_uris": ["https://a/cb"]})
        assert r1.status_code == 401  # session gate, rate check passed
        assert r2.status_code == 401
        # 3rd exceeds the global ceiling -> 429 BEFORE the session gate.
        r3 = client.post("/oauth/register", json={"redirect_uris": ["https://a/cb"]})
        assert r3.status_code == 429
        assert r3.json()["error"] == "rate_limit_exceeded"
        assert int(r3.headers["Retry-After"]) >= 1


def test_default_build_app_has_a_limiter_wired():
    """build_app must wire a limiter by default (not None) so prod is
    never unprotected if the caller forgets."""
    rl = GlobalRateLimiter(limit=1, window_seconds=300)
    with TestClient(build_app(dcr_rate_limiter=rl)) as client:
        client.post("/oauth/register", json={"redirect_uris": ["https://a/cb"]})
        blocked = client.post("/oauth/register", json={"redirect_uris": ["https://a/cb"]})
        assert blocked.status_code == 429
