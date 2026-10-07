"""HTTP-level flow tests against real PostgreSQL (fixture: tests/conftest.py).

These go through the ASGI app with a fresh session per request — the same way
the frontend talks to the API — so cross-request persistence bugs (for example
a save that the next request's saved_only query cannot see) are caught here.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.models import Institution, Program


@pytest.fixture
async def api(db_session: Any) -> AsyncClient:
    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


async def _make_program(session: Any, slug: str) -> Program:
    institution = Institution(
        canonical_name=f"Flow University {slug}",
        normalized_name=f"flow university {slug}",
        domain=f"{slug.lower()}.example.edu",
    )
    session.add(institution)
    await session.flush()
    program = Program(
        institution_id=institution.id,
        canonical_name=f"M.Sc. Flow Testing {slug}",
        normalized_name=f"m.sc. flow testing {slug.lower()}",
    )
    session.add(program)
    await session.commit()
    return program


async def test_save_is_visible_to_next_request_saved_only_list(
    api: AsyncClient, db_session: Any
) -> None:
    """Regression: POST save must be immediately visible to GET ?saved_only=true."""
    program = await _make_program(db_session, "Roundtrip")
    pid = str(program.id)

    r = await api.post(f"/api/v1/programs/{pid}/save")
    assert r.status_code == 200, r.text
    assert r.json() == {"saved": True, "program_id": pid}

    r = await api.get("/api/v1/programs", params={"saved_only": "true"})
    assert r.status_code == 200, r.text
    assert pid in {item["id"] for item in r.json()["items"]}

    r = await api.delete(f"/api/v1/programs/{pid}/save")
    assert r.status_code == 200, r.text
    assert r.json()["saved"] is False

    r = await api.get("/api/v1/programs", params={"saved_only": "true"})
    assert r.status_code == 200, r.text
    assert pid not in {item["id"] for item in r.json()["items"]}


async def test_monitor_subscription_list_names_the_watched_program(
    api: AsyncClient, db_session: Any
) -> None:
    """Subscriptions created from a program page must say which program they watch."""
    program = await _make_program(db_session, "MonitorWatch")
    pid = str(program.id)

    r = await api.post(
        "/api/v1/monitor/subscriptions",
        json={"field_key": "deadline", "frequency": "WEEKLY", "program_id": pid},
    )
    assert r.status_code == 200, r.text
    sub_id = r.json()["id"]

    r = await api.get("/api/v1/monitor/subscriptions")
    assert r.status_code == 200, r.text
    item = next(i for i in r.json()["items"] if i["id"] == sub_id)
    assert item["program_id"] == pid
    assert item["program_name"] == "M.Sc. Flow Testing MonitorWatch"
    assert item["enabled"] is True
