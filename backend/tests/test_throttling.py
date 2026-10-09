"""Unit tests for app.core.throttling.FailureThrottle (login-lockout helper).

The helper ships before its wiring: app/api/v1/auth.py is owned by another
workstream, which (or the integrator) must call is_limited/record_failure/
reset around the password check. These tests pin the semantics the auth path
will rely on: 10 failures / 15 min per key, window from the first failure,
clean reset on success, bounded memory, thread-safe access.
"""

from __future__ import annotations

import threading

import pytest

from app.core.throttling import FailureThrottle


class FakeClock:
    """Deterministic monotonic clock: tests advance time instead of sleeping."""

    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_locks_after_max_failures_within_window() -> None:
    clock = FakeClock()
    throttle = FailureThrottle(max_failures=3, window_seconds=900, clock=clock)
    email = "student@example.com"

    assert throttle.is_limited(email) is False
    assert throttle.remaining(email) == 3

    assert throttle.record_failure(email) == 1
    assert throttle.is_limited(email) is False
    assert throttle.remaining(email) == 2

    assert throttle.record_failure(email) == 2
    assert throttle.record_failure(email) == 3
    assert throttle.is_limited(email) is True
    assert throttle.remaining(email) == 0
    assert 0 < throttle.seconds_until_unlock(email) <= 900


def test_window_expiry_unlocks_and_reclaims() -> None:
    clock = FakeClock()
    throttle = FailureThrottle(max_failures=2, window_seconds=60, clock=clock)
    throttle.record_failure("a@example.com")
    throttle.record_failure("a@example.com")
    assert throttle.is_limited("a@example.com") is True
    assert throttle.tracked_keys() == 1

    clock.advance(61)
    assert throttle.is_limited("a@example.com") is False
    assert throttle.remaining("a@example.com") == 2
    # The expired bucket is reclaimed from the map on access.
    assert throttle.tracked_keys() == 0


def test_success_resets_the_window() -> None:
    throttle = FailureThrottle(max_failures=2, window_seconds=900, clock=FakeClock())
    throttle.record_failure("a@example.com")
    throttle.record_failure("a@example.com")
    assert throttle.is_limited("a@example.com") is True

    throttle.reset("a@example.com")  # successful login clears the lockout
    assert throttle.is_limited("a@example.com") is False
    assert throttle.remaining("a@example.com") == 2
    assert throttle.tracked_keys() == 0


def test_keys_are_independent() -> None:
    throttle = FailureThrottle(max_failures=2, window_seconds=900, clock=FakeClock())
    for _ in range(2):
        throttle.record_failure("attacker@example.com")
    assert throttle.is_limited("attacker@example.com") is True
    assert throttle.is_limited("victim@example.com") is False
    assert throttle.remaining("victim@example.com") == 2
    assert throttle.is_limited("1.2.3.4") is False  # any key shape works


def test_memory_stays_bounded() -> None:
    clock = FakeClock()
    throttle = FailureThrottle(max_failures=3, window_seconds=60, max_keys=5, clock=clock)
    for i in range(50):
        throttle.record_failure(f"user{i}@example.com")
    assert throttle.tracked_keys() <= 5, "bucket map exceeded max_keys"

    # Expired buckets are swept on the next write as well.
    clock.advance(61)
    throttle.record_failure("fresh@example.com")
    assert throttle.tracked_keys() == 1


def test_thread_safety_smoke() -> None:
    throttle = FailureThrottle(max_failures=10_000, window_seconds=900, clock=FakeClock())
    errors: list[BaseException] = []

    def hammer() -> None:
        try:
            for _ in range(200):
                throttle.record_failure("shared@example.com")
                throttle.is_limited("shared@example.com")
        except BaseException as exc:  # noqa: BLE001 - surfaced via the assert below
            errors.append(exc)

    threads = [threading.Thread(target=hammer) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert throttle.tracked_keys() == 1


def test_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError):
        FailureThrottle(max_failures=0)
    with pytest.raises(ValueError):
        FailureThrottle(window_seconds=0)
