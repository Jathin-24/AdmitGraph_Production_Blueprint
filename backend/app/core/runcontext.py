"""Correlation ids carried through ContextVars for structured logs.

BACKEND_SPEC §Reliability: "structured logs with request_id and run_id".

- request_id: set once per request by the request-id middleware (main.py);
  every log line emitted while serving that request carries it.
- run_id: bound around the research dispatch (orchestrator.dispatch_plan);
  the asyncio Task copies the context, so all orchestrator logs for that run
  carry run_id.

Both default to an empty string outside their scope, so log records are
always well-formed without any None handling at the call site.
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager

_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
_run_id: contextvars.ContextVar[str] = contextvars.ContextVar("run_id", default="")


def current_request_id() -> str:
    """Request id of the request being served ("" outside a request)."""
    return _request_id.get()


def current_run_id() -> str:
    """Research run id bound to the current dispatch ("" outside a run)."""
    return _run_id.get()


@contextmanager
def request_id_scope(value: str) -> Iterator[None]:
    """Bind request_id for the duration of the block; restore the previous value."""
    token = _request_id.set(value)
    try:
        yield
    finally:
        _request_id.reset(token)


@contextmanager
def run_id_scope(value: str) -> Iterator[None]:
    """Bind run_id for the duration of the block; restore the previous value."""
    token = _run_id.set(value)
    try:
        yield
    finally:
        _run_id.reset(token)
