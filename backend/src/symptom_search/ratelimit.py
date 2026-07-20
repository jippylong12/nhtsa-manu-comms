"""A tiny in-memory sliding-window rate limiter.

Personal-use scale: one process, no Redis. Each symptom query costs an embedding
plus an LLM call, so this exists to cap accidental runaway (a stuck retry loop,
a held-down key), not to defend against real abuse. Keyed by client host.
"""

import threading
import time
from collections import defaultdict, deque


class SlidingWindowLimiter:
    def __init__(self, max_requests: int, window_seconds: float) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        """Record a hit and return whether it is within the limit."""
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            dq = self._hits[key]
            while dq and dq[0] <= cutoff:
                dq.popleft()
            if len(dq) >= self.max_requests:
                return False
            dq.append(now)
            return True

    def retry_after(self, key: str) -> int:
        """Seconds until the oldest hit in the window expires (for Retry-After)."""
        now = time.monotonic()
        with self._lock:
            dq = self._hits.get(key)
            if not dq:
                return 0
            return max(1, int(self.window_seconds - (now - dq[0])))
