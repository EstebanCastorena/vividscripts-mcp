"""Global (not per-IP) rolling-window rate limiter — KAN-83.

Defense-in-depth for ``POST /oauth/register``. Deliberately **global**,
not per-client-IP: behind CloudFront + an internet-facing ALB the
client IP can only be derived from a spoofable / position-ambiguous
``X-Forwarded-For`` (CloudFront forwards client headers; the ALB is
directly reachable), so a per-IP app-layer limit would be bypassable —
false security. Sound per-IP limiting is done at the edge by the AWS
WAF rate-based rule (slide_editor ``terraform/17-waf.tf``); this
process-wide ceiling is the in-app backstop that needs no IP trust and
is deterministically testable. See the Obsidian *MCP Phase 4 KAN-83
Rate-Limit Blocker Analysis* for the full topology reasoning.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable


class GlobalRateLimiter:
    """Thread-safe process-wide rolling-window counter.

    Not keyed by anything — one bucket for the whole endpoint. The cap
    is set high enough never to impede legitimate use (DCR happens ~once
    per client install) while bounding catastrophic scripted abuse if
    the edge WAF were mis/de-configured.
    """

    def __init__(
        self,
        limit: int = 100,
        window_seconds: int = 300,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        if window_seconds < 1:
            raise ValueError("window_seconds must be >= 1")
        self._limit = limit
        self._window = window_seconds
        self._clock = clock
        self._events: deque[float] = deque()
        self._lock = threading.Lock()

    def check(self) -> int | None:
        """Record an attempt. Return ``None`` if allowed, else the
        ``Retry-After`` seconds (>=1) when the limit is exceeded.

        A rejected attempt is NOT recorded, so a sustained flood can't
        keep pushing the window forward and starve recovery.
        """
        now = self._clock()
        with self._lock:
            cutoff = now - self._window
            while self._events and self._events[0] <= cutoff:
                self._events.popleft()
            if len(self._events) >= self._limit:
                retry_after = self._events[0] + self._window - now
                return max(int(retry_after) + 1, 1)
            self._events.append(now)
            return None
