"""Cross-replica PostgreSQL advisory locks for the background workers.

Audit P1-7: the scheduler used a process-local flag, so every replica that
ran the FastAPI lifespan ticked at the same time (duplicate monitor checks,
duplicate notifications, wasted SerpApi budget) and the startup recovery pass
ran unconditionally on every boot.

Advisory locks (`pg_advisory_lock(key)`) are cluster-wide and owned by the
CONNECTION that took them, so each helper below pins one dedicated connection
for the whole `async with` block: whoever gets `True` does the exclusive work,
whoever gets `False` skips cheaply. The lock is always released explicitly —
closing alone would return the connection to the pool still holding the
session-level lock — and if the unlock fails the connection is invalidated so
the server session (and its lock) is torn down.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

log = logging.getLogger(__name__)

# Fixed application keys in the advisory-lock namespace. Distinct keys let the
# scheduler tick and the startup recovery pass exclude each other independently.
SCHEDULER_TICK_LOCK = 745_000_001
RECOVERY_LOCK = 745_000_002


@asynccontextmanager
async def try_advisory_lock(engine: AsyncEngine, key: int) -> AsyncIterator[bool]:
    """Yield True if `key` was free (lock now held), False if it is taken.

    Non-blocking by design: background workers must never queue behind each
    other — they skip the tick instead.
    """
    connection: AsyncConnection | None = None
    acquired = False
    try:
        connection = await engine.connect()
        result = await connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
        acquired = bool(result.scalar_one())
        yield acquired
    finally:
        if connection is not None:
            released = True
            if acquired:
                try:
                    await connection.execute(
                        text("SELECT pg_advisory_unlock(:key)"), {"key": key}
                    )
                except Exception:  # noqa: BLE001 - fall back to killing the session
                    released = False
                    log.exception("advisory unlock failed for key %s", key)
            if not released:
                # `close()` returns the connection to the POOL with the
                # session-level lock still held; invalidate() closes the
                # underlying server session so the lock cannot leak.
                try:
                    await connection.invalidate()
                except Exception:  # noqa: BLE001 - shutdown must not mask errors
                    log.exception("connection invalidate failed for key %s", key)
            else:
                try:
                    await connection.close()
                except Exception:  # noqa: BLE001 - shutdown must not mask the caller's error
                    log.exception("connection close failed after advisory lock key %s", key)
