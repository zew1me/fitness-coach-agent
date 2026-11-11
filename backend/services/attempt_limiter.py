"""Best-effort, per-instance limiter for credential-guessing endpoints.

State lives in process memory, so each serverless instance counts independently and a cold
start forgets history. That is weaker than a shared store, but it still bounds how fast one
instance can be driven to guess codes for a single key, and it adds no table or round trip.
Supabase Auth's own verification limit is per client IP, and every server-side call arrives
from our egress IPs, so it cannot provide per-account protection on its own.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class _Window:
    started_at: float
    attempts: int


class AttemptLimiter:
    """Allow at most ``max_attempts`` per key in a fixed window; success clears the key."""

    def __init__(
        self,
        *,
        max_attempts: int,
        window_seconds: float,
        max_keys: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_attempts = max_attempts
        self._window_seconds = window_seconds
        self._max_keys = max_keys
        self._clock = clock
        self._windows: OrderedDict[str, _Window] = OrderedDict()

    def try_acquire(self, key: str) -> float | None:
        """Record an attempt; return ``None`` if allowed, else seconds until the window resets.

        The attempt is counted before the guarded call runs, so concurrent requests on one
        instance cannot all pass the check before any of them records a failure.
        """
        now = self._clock()
        window = self._windows.get(key)
        if window is None or now - window.started_at >= self._window_seconds:
            window = _Window(started_at=now, attempts=0)
        if window.attempts >= self._max_attempts:
            return max(0.0, self._window_seconds - (now - window.started_at))
        window.attempts += 1
        self._windows[key] = window
        self._windows.move_to_end(key)
        while len(self._windows) > self._max_keys:
            self._windows.popitem(last=False)
        return None

    def reset(self, key: str) -> None:
        self._windows.pop(key, None)
