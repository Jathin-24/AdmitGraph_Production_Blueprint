"""Unhandled 500s keep their correlation (audit P2-27).

The handler used to return request_id in the body but log nothing, and the
exception escaped the request-id middleware scope — server logs showed an
empty request_id and the response carried no X-Request-ID header.
"""

from __future__ import annotations

import io
import json
import logging

from fastapi.testclient import TestClient

from app.core.logging import JsonFormatter
from app.main import app

_BOOM_PATH = "/__test_boom_500"


def _install_boom_route() -> None:
    @app.get(_BOOM_PATH)
    async def _boom() -> None:
        raise RuntimeError("kaboom-for-test")


def _remove_boom_route() -> None:
    app.router.routes[:] = [
        route for route in app.router.routes if getattr(route, "path", None) != _BOOM_PATH
    ]


def test_unhandled_500_is_logged_with_request_id_and_carries_the_header() -> None:
    _install_boom_route()
    errors_logger = logging.getLogger("app.core.errors")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    errors_logger.addHandler(handler)
    try:
        # raise_server_exceptions=True on purpose: the exception must NOT
        # escape the middleware — the 500 is rendered inside the request-id
        # scope, otherwise this call would raise instead of returning.
        response = TestClient(app).get(_BOOM_PATH, headers={"X-Request-ID": "req-500-test"})
    finally:
        errors_logger.removeHandler(handler)
        _remove_boom_route()

    # Response: correlated 500 envelope + middleware headers.
    assert response.status_code == 500
    assert response.headers.get("X-Request-ID") == "req-500-test"
    assert response.headers.get("X-Content-Type-Options") == "nosniff"  # rendered via the middleware
    body = response.json()
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert body["error"]["request_id"] == "req-500-test"

    # Log: one ERROR record carrying the same request id and the traceback.
    lines = [line for line in stream.getvalue().splitlines() if line.strip()]
    assert lines, "unhandled error was not logged at all"
    payload = json.loads(lines[-1])
    assert payload["level"] == "ERROR"
    assert payload["request_id"] == "req-500-test"
    assert payload["logger"] == "app.core.errors"
    assert "RuntimeError: kaboom-for-test" in payload["exception"]
