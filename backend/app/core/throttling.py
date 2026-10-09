"""In-memory failure throttle for sensitive actions (failed logins, ...).

Wiring note (ownership): the login path lives in ``app/api/v1/auth.py``,
owned by workstream W3, so this helper is exposed here for that router (or
the integrator) to wire up:

    throttle = FailureThrottle(max_failures=10, window_seconds=15 * 60)

    if throttle.is_limited(normalized_email):
        raise AppError(429, "TOO_MANY_ATTEMPTS", "Too many failed attempts; try again later")
    ... verify credentials ...
    if ok:
        throttle.reset(normalized_email)
    else:
        throttle.record_failure(normalized_email)

Per-process only: buckets live in this process's memory, so N worker
processes/replicas mean N independent budgets. A deployment that needs a
globally enforced lockout must back the same interface with a shared store
(Redis is already a project dependency).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


class FailureThrottle:
    """Allow at most ``max_failures`` failures per key within ``window_seconds``.

    The window starts at a key's first failure; once it elapses the key is
    clean again (expired buckets are reclaimed on access and swept from the
    map so memory stays bounded). Keys are opaque strings — typically a
    normalized email or a client IP. Thread-safe; the clock is injectable so
    tests never sleep.
    """

    def __init__(
        self,
        max_failures: int = 10,
        window_seconds: float = 15 * 60,
        max_keys: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_failures < 1:
            raise ValueError("max_failures must be >= 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._clock = clock
        self._lock = threading.Lock()
        # key -> (failure count, window start)
        self._failures: dict[str, tuple[int, float]] = {}

    def _current(self, key: str, now: float) -> tuple[int, float]:
        """(count, window_start) for key at `now`, dropping an expired window."""
        stored = self._failures.get(key)
        if stored is not None:
            count, started = stored
            if now - started <= self.window_seconds:
                return count, started
            del self._failures[key]
        return 0, now

    def _evict(self, now: float) -> None:
        """Bound the map: expired windows first, then oldest live windows."""
        expired = [
            key
            for key, (_count, started) in self._failures.items()
            if now - started > self.window_seconds
        ]
        for key in expired:
            del self._failures[key]
        excess = len(self._failures) - self.max_keys
        if excess > 0:
            oldest_first = sorted(self._failures.items(), key=lambda item: item[1][1])
            for key, _value in oldest_first[:excess]:
                del self._failures[key]

    def is_limited(self, key: str) -> bool:
        """True when the key has burned its failure budget for this window."""
        with self._lock:
            count, _started = self._current(key, self._clock())
            return count >= self.max_failures

    def record_failure(self, key: str) -> int:
        """Count one failure; returns the key's failure count in this window."""
        with self._lock:
            now = self._clock()
            count, started = self._current(key, now)
            count += 1
            self._failures[key] = (count, started)
            self._evict(now)
            return count

    def reset(self, key: str) -> None:
        """Clear a key's failures (call on a successful authentication)."""
        with self._lock:
            self._failures.pop(key, None)

    def remaining(self, key: str) -> int:
        """Failures the key may still spend before being locked out."""
        with self._lock:
            count, _started = self._current(key, self._clock())
            return max(0, self.max_failures - count)

    def seconds_until_unlock(self, key: str) -> float:
        """Seconds until the key's window expires (0 when not limited)."""
        with self._lock:
            now = self._clock()
            count, started = self._current(key, now)
            if count < self.max_failures:
                return 0.0
            return max(0.0, (started + self.window_seconds) - now)

    def tracked_keys(self) -> int:
        """Number of buckets currently held (bounded by `max_keys`)."""
        with self._lock:
            return len(self._failures)
