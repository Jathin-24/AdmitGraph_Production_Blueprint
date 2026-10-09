"""GET /metrics in Prometheus text format (audit P2-27).

The registry is process-wide (shared with every other test in the session),
so assertions use deltas/patterns, never absolute session totals.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from fastapi.testclient import TestClient

from app.core import limits, metrics
from app.main import app


def _counter_value(method: str, route: str, status: str) -> float:
    for sample in metrics.HTTP_REQUESTS.collect()[0].samples:
        if sample.name == "http_requests_total" and sample.labels == {
            "method": method,
            "route": route,
            "status": status,
        }:
            return float(sample.value)
    return 0.0


def test_metrics_endpoint_serves_prometheus_text_format() -> None:
    client = TestClient(app)
    assert client.get("/api/v1/health").status_code == 200

    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers.get("X-Request-ID"), "the endpoint must sit behind the middleware"

    text = response.text
    assert "# HELP http_requests_total" in text
    assert "# TYPE http_requests_total counter" in text
    assert "# TYPE http_request_duration_seconds histogram" in text
    assert "# TYPE research_runs_active gauge" in text
    # The health request above was counted, labelled with its route TEMPLATE
    # and method (labels: method, route, status).
    assert re.search(r'http_requests_total\{method="GET",route="/health",status="200"\} [1-9]', text)
    assert re.search(r'http_request_duration_seconds_bucket\{[^}]*\} \d', text)
    assert re.search(r"research_runs_active \d", text)


def test_requests_are_counted_by_route_and_status() -> None:
    client = TestClient(app)
    before = _counter_value("GET", "/health", "200")

    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/health").status_code == 200

    assert _counter_value("GET", "/health", "200") == before + 2

    # Each scrape observes itself once it has been served (visible on the next scrape).
    client.get("/metrics")
    text = client.get("/metrics").text
    match = re.search(r'http_requests_total\{method="GET",route="/metrics",status="200"\} ([\d.]+)', text)
    assert match is not None and float(match.group(1)) >= 2


def test_unrouted_paths_never_use_raw_paths_as_labels() -> None:
    """404s happen before routing; the raw path is unbounded and must not leak into labels."""
    client = TestClient(app)
    assert client.get("/definitely/not/a/route-xyz").status_code == 404

    text = client.get("/metrics").text
    assert 'route="unrouted"' in text
    assert "route-xyz" not in text


async def test_research_runs_gauge_follows_the_limiter(monkeypatch: Any) -> None:
    limiter = limits.RunLimiter(max_global=2, max_per_user=1)
    monkeypatch.setattr(limits, "get_limiter", lambda: limiter)

    assert b"research_runs_active 0.0" in metrics.render()

    user = uuid.uuid4()
    async with limiter.slot(user):
        assert b"research_runs_active 1.0" in metrics.render()

    assert b"research_runs_active 0.0" in metrics.render()
