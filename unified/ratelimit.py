"""Token bucket rate limiter (per client). Research: token bucket allows
controlled bursts while enforcing a sustained rate; 429 + Retry-After."""
from __future__ import annotations

import threading
import time


class TokenBucket:
    def __init__(self, capacity: int = 30, refill_per_sec: float = 5.0):
        self.capacity = float(capacity)
        self.refill = refill_per_sec
        self._tokens = float(capacity)
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def allow(self) -> tuple[bool, int]:
        """Returns (allowed, retry_after_seconds)."""
        with self._lock:
            now = time.monotonic()
            self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.refill)
            self._last = now
            if self._tokens >= 1:
                self._tokens -= 1
                return True, 0
            if self.refill <= 0:
                return False, 60
            return False, max(1, int((1 - self._tokens) / self.refill))
