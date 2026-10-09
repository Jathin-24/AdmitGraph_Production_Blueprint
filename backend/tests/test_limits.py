"""Unit tests for the research-run concurrency caps (audit P1-7 / PLAN P1-7).

Pure asyncio tests — no database, no HTTP: the limiter is plain counters
guarded by one asyncio.Condition (see app/core/limits.py).
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.core import limits


def test_limits_configure_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(limits.ENV_MAX_GLOBAL_RUNS, "6")
    monkeypatch.setenv(limits.ENV_MAX_RUNS_PER_USER, "3")
    limits.reset_limiter()
    try:
        limiter = limits.get_limiter()
        assert limiter.max_global == 6
        assert limiter.max_per_user == 3
        assert limits.get_limiter() is limiter  # singleton
    finally:
        limits.reset_limiter()


def test_limits_ignore_garbage_environment_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(limits.ENV_MAX_GLOBAL_RUNS, "many")
    monkeypatch.setenv(limits.ENV_MAX_RUNS_PER_USER, "0")  # must be >= 1
    limits.reset_limiter()
    try:
        limiter = limits.get_limiter()
        assert limiter.max_global == limits.DEFAULT_MAX_GLOBAL_RUNS
        assert limiter.max_per_user == limits.DEFAULT_MAX_RUNS_PER_USER
    finally:
        limits.reset_limiter()


async def test_global_cap_admits_at_most_max_concurrent() -> None:
    limiter = limits.RunLimiter(max_global=2, max_per_user=1)
    active = 0
    peak = 0

    async def worker() -> None:
        nonlocal active, peak
        async with limiter.slot(uuid.uuid4()):
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.02)
            active -= 1

    await asyncio.gather(*(worker() for _ in range(8)))

    assert peak == 2
    assert limiter.active_global == 0
    assert limiter._active_per_user == {}  # idle users drop out of the map


async def test_per_user_cap_blocks_same_user_but_not_others() -> None:
    limiter = limits.RunLimiter(max_global=10, max_per_user=1)
    user_a, user_b = uuid.uuid4(), uuid.uuid4()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def holder() -> None:
        async with limiter.slot(user_a):
            entered.set()
            await release.wait()

    holder_task = asyncio.create_task(holder())
    await entered.wait()

    # Same user: must wait (the global cap has plenty of room).
    blocked = asyncio.create_task(limiter.acquire(user_a))
    await asyncio.sleep(0.01)
    assert not blocked.done(), "per-user cap did not block a second run of the same user"

    # Different user: admitted immediately.
    await asyncio.wait_for(limiter.acquire(user_b), timeout=1.0)
    assert limiter.active_for(user_b) == 1
    await limiter.release(user_b)

    # Freeing the first slot lets the blocked run through.
    release.set()
    await holder_task
    await asyncio.wait_for(blocked, timeout=1.0)
    assert limiter.active_for(user_a) == 1
    await limiter.release(user_a)
    assert limiter.active_global == 0
    assert limiter._active_per_user == {}


async def test_slot_releases_on_exception() -> None:
    limiter = limits.RunLimiter(max_global=1, max_per_user=1)
    user = uuid.uuid4()

    with pytest.raises(ValueError, match="boom"):
        async with limiter.slot(user):
            raise ValueError("boom")

    assert limiter.active_global == 0
    # The cap is usable again — nothing was leaked by the failure.
    async with limiter.slot(user):
        assert limiter.active_global == 1


async def test_all_waiters_eventually_run_without_starvation() -> None:
    limiter = limits.RunLimiter(max_global=2, max_per_user=1)
    done: list[int] = []

    async def worker(i: int) -> None:
        async with limiter.slot(uuid.uuid4()):
            await asyncio.sleep(0.005)
            done.append(i)

    # 12 runs, distinct users: only 2 at a time, but every one must finish.
    await asyncio.wait_for(asyncio.gather(*(worker(i) for i in range(12))), timeout=10.0)
    assert sorted(done) == list(range(12))
    assert limiter.active_global == 0
