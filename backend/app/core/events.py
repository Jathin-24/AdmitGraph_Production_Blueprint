"""Lightweight in-process event bus.

Decouples producers (research orchestrator, monitor checks, scheduler) from
listeners (notifications, emails) so those features can be added or changed
without editing the producer modules. Listeners run as fire-and-forget tasks:
they can never fail or block the request that emitted the event.

Usage:
    from app.core.events import emit, on

    async def on_run_finished(plan_id: str, **_: object) -> None: ...
    on("research.run_finished", on_run_finished)   # register at import time
    emit("research.run_finished", plan_id=str(plan_id))  # from anywhere
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger(__name__)

Listener = Callable[..., Awaitable[None]]

_listeners: dict[str, list[Listener]] = defaultdict(list)


def on(event: str, listener: Listener) -> None:
    """Register an async listener for an event name (idempotent per function)."""
    if listener not in _listeners[event]:
        _listeners[event].append(listener)


def emit(event: str, **payload: Any) -> None:
    """Schedule all listeners for *event*; failures are logged, never raised."""
    listeners = _listeners.get(event, [])
    if not listeners:
        return
    for listener in listeners:
        try:
            task = asyncio.create_task(_safe_run(event, listener, payload))
        except RuntimeError:
            # No running loop (sync context): run best-effort in a fresh loop.
            log.debug("emit(%s) without running loop; skipping listener", event)
            return
        task.add_done_callback(lambda _t: None)


async def _safe_run(event: str, listener: Listener, payload: dict[str, Any]) -> None:
    try:
        await listener(**payload)
    except Exception:  # noqa: BLE001 - listeners must never break producers
        log.exception("event listener failed: %s", event)
