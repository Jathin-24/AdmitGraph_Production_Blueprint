"""Structured JSON logs with request_id/run_id (BACKEND_SPEC §Reliability)."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import uuid

from app.core.logging import JsonFormatter
from app.core.runcontext import current_request_id, current_run_id, request_id_scope, run_id_scope


def _handler_for(logger: logging.Logger, stream: io.StringIO) -> logging.StreamHandler:
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    return handler


def test_log_record_is_valid_json_with_resolved_percent_args() -> None:
    logger = logging.getLogger("app.tests.platform_logging")
    stream = io.StringIO()
    handler = _handler_for(logger, stream)
    try:
        logger.info("hello %s & welcome", "world")
    finally:
        logger.removeHandler(handler)

    line = stream.getvalue().strip()
    payload = json.loads(line)  # raises unless the record is valid JSON
    assert line.startswith("{") and line.endswith("}")
    assert payload["message"] == "hello world & welcome"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.tests.platform_logging"


def test_log_record_carries_request_id_and_run_id_from_contextvars() -> None:
    logger = logging.getLogger("app.tests.platform_scoped")
    stream = io.StringIO()
    handler = _handler_for(logger, stream)
    try:
        with request_id_scope("req-abc-123"), run_id_scope("plan-456"):
            logger.info("inside scope")
        inside = json.loads(stream.getvalue().strip())
        assert inside["request_id"] == "req-abc-123"
        assert inside["run_id"] == "plan-456"

        stream.truncate(0)
        stream.seek(0)
        logger.info("outside scope")
        outside = json.loads(stream.getvalue().strip())
        # Unset contextvars default to empty strings, never null or missing.
        assert outside["request_id"] == ""
        assert outside["run_id"] == ""
    finally:
        logger.removeHandler(handler)


def test_context_ids_reset_and_nest_correctly() -> None:
    assert current_request_id() == ""
    assert current_run_id() == ""
    with request_id_scope("outer"):
        with request_id_scope("inner"):
            assert current_request_id() == "inner"
        assert current_request_id() == "outer"
    assert current_request_id() == ""


def test_dispatch_plan_binds_run_id_inside_the_task(monkeypatch) -> None:
    """dispatch_plan keeps its signature; the Task context carries run_id."""
    from app.services.research import orchestrator

    seen: list[str] = []

    class FakeService:
        """Test double for ResearchService: records the run_id it observes."""

        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def execute_plan(self, plan_id: uuid.UUID) -> None:
            seen.append(current_run_id())

    monkeypatch.setattr(orchestrator, "ResearchService", FakeService)

    async def run() -> None:
        plan_id = uuid.uuid4()
        orchestrator.dispatch_plan(plan_id)
        # The caller's context is untouched; only the task inherits run_id.
        assert current_run_id() == ""
        for _ in range(100):
            if seen:
                break
            await asyncio.sleep(0.01)
        assert seen == [str(plan_id)]
        await asyncio.sleep(0.05)  # let the done-callbacks run in this loop

    asyncio.run(run())
