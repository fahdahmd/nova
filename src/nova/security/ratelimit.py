"""A dependency-free sliding-window rate limiter.

Used for HTTP requests (keyed by client IP), WebSocket control messages and
audio frames (keyed by connection id). Keeping it in-process is appropriate:
Nova is a single-user local application.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from threading import Lock


class RateLimitExceeded(Exception):  # noqa: N818 - conventional name for this condition
    """Raised when a key exhausts its allowance for the current window."""

    def __init__(self, key: str, retry_after: float) -> None:
        super().__init__(f"rate limit exceeded for {key!r}; retry in {retry_after:.1f}s")
        self.key = key
        self.retry_after = retry_after


@dataclass
class RateLimiter:
    """Allow at most ``limit`` events per ``window_s`` for each key."""

    limit: int
    window_s: float
    _events: dict[str, deque[float]] = field(default_factory=lambda: defaultdict(deque))
    _lock: Lock = field(default_factory=Lock)

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise ValueError("limit must be >= 1")
        if self.window_s <= 0:
            raise ValueError("window_s must be > 0")

    def _prune(self, bucket: deque[float], now: float) -> None:
        cutoff = now - self.window_s
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()

    def check(self, key: str) -> None:
        """Record an event, or raise ``RateLimitExceeded``."""
        now = time.monotonic()
        with self._lock:
            bucket = self._events[key]
            self._prune(bucket, now)
            if len(bucket) >= self.limit:
                retry_after = max(0.0, self.window_s - (now - bucket[0]))
                raise RateLimitExceeded(key, retry_after)
            bucket.append(now)

    def allow(self, key: str) -> bool:
        """Non-raising variant of :meth:`check`."""
        try:
            self.check(key)
        except RateLimitExceeded:
            return False
        return True

    def remaining(self, key: str) -> int:
        now = time.monotonic()
        with self._lock:
            bucket = self._events[key]
            self._prune(bucket, now)
            return max(0, self.limit - len(bucket))

    def reset(self, key: str | None = None) -> None:
        """Forget one key, or all of them (used when a connection closes)."""
        with self._lock:
            if key is None:
                self._events.clear()
            else:
                self._events.pop(key, None)
