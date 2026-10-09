"""Prometheus metrics for the API (audit P2-27).

This module owns the registry, the collectors and the text renderer;
instrumentation happens in the request-id middleware in ``app/main.py``,
which already knows method/route/status/duration for every response.

- ``http_requests_total``            counter   by method / route / status
- ``http_request_duration_seconds``  histogram by method / route / status
- ``research_runs_active``           gauge     (live value from app.core.limits)

Exposed at ``GET /metrics`` in the Prometheus text exposition format
(version 0.0.4, ``prometheus_client.CONTENT_TYPE_LATEST``).
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

# Dedicated registry (not the prometheus_client default): no global state is
# shared with libraries that might self-register, and tests get a stable,
# import-once collector set.
REGISTRY = CollectorRegistry()

# Label for responses produced before routing (401/413/429 rejections, 404s):
# the raw path must NOT be used as a label — it is unbounded and would let
# callers explode metric cardinality.
UNROUTED = "unrouted"

HTTP_REQUESTS = Counter(
    "http_requests_total",
    "HTTP requests served, by method, route and status code.",
    ["method", "route", "status"],
    registry=REGISTRY,
)

HTTP_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds, by method, route and status code.",
    ["method", "route", "status"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    registry=REGISTRY,
)


def _active_research_runs() -> float:
    # Imported lazily: app.core.limits must stay import-light and neither
    # module needs the other at import time.
    from app.core.limits import get_limiter

    return float(get_limiter().active_global)


RESEARCH_RUNS_ACTIVE = Gauge(
    "research_runs_active",
    "Research runs currently holding an execution slot in this process.",
    registry=REGISTRY,
)
RESEARCH_RUNS_ACTIVE.set_function(_active_research_runs)


def observe_request(method: str, route: str, status: int, duration_seconds: float) -> None:
    """Record one served request into the counters/histogram."""
    labels = {"method": method, "route": route, "status": str(status)}
    HTTP_REQUESTS.labels(**labels).inc()
    HTTP_DURATION.labels(**labels).observe(duration_seconds)


def observe(request: Request, status: int, duration_seconds: float) -> None:
    """Record a response using the matched route template (never the raw path)."""
    route = request.scope.get("route")
    observe_request(request.method, getattr(route, "path", None) or UNROUTED, status, duration_seconds)


def render() -> bytes:
    """Current registry snapshot in the Prometheus text exposition format."""
    return generate_latest(REGISTRY)


async def metrics_endpoint() -> Response:
    """GET /metrics — Prometheus scrape target (behind the normal middleware)."""
    return Response(content=render(), media_type=CONTENT_TYPE_LATEST)
