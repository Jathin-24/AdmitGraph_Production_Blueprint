"""W12 application tracker over HTTP.

Integration tests against real PostgreSQL (fixture: tests/conftest.py, run
with AGRAPH_TEST_DATABASE_URL=…/admitgraph_test_tracker so concurrent
workstreams never share a scratch database).

Pins the PLAN.md W12 contract:
* create / list / patch / delete round trip with every documented field;
* an unknown or missing ``status`` is a 422 VALIDATION_ERROR envelope,
  never a 500;
* list honours the optional ``?status=`` filter — and an unknown filter
  value is a 422, not a silently empty list;
* row-level security: user B gets a 404 (indistinguishable from a missing
  row) for user A's application on read (A's rows never appear in B's
  list) and on mutate (PATCH and DELETE).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def api(db_session: Any) -> AsyncIterator[AsyncClient]:
    from app import main as main_module
    from app.main import app

    main_module._rate_counters.clear()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


async def _register(api: AsyncClient, prefix: str) -> dict[str, str]:
    response = await api.post(
        "/api/v1/auth/register",
        json={
            "email": f"{prefix}-{uuid.uuid4().hex[:8]}@example.com",
            "password": "correct-horse-1",
        },
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


async def _create(api: AsyncClient, headers: dict[str, str], **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"university": "TU Munich", "status": "draft"}
    body.update(overrides)
    response = await api.post("/api/v1/applications", json=body, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _error(response: Any) -> dict[str, Any]:
    body = response.json()
    assert "error" in body, body
    return body["error"]


async def _list(api: AsyncClient, headers: dict[str, str], **params: str) -> list[dict[str, Any]]:
    response = await api.get("/api/v1/applications", params=params, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["items"]


# ------------------------------------------------------------- happy path


async def test_create_list_patch_delete_roundtrip(api: AsyncClient) -> None:
    headers = await _register(api, "app-crud")
    created = await _create(
        api,
        headers,
        program_name="M.Sc. Robotics",
        status="submitted",
        url="https://example.edu/apply",
        notes="Translated transcript sent",
        submitted_at="2026-01-15T09:30:00Z",
    )
    uuid.UUID(created["id"])
    assert created["university"] == "TU Munich"
    assert created["program_name"] == "M.Sc. Robotics"
    assert created["status"] == "submitted"
    assert created["url"] == "https://example.edu/apply"
    assert created["notes"] == "Translated transcript sent"
    assert created["submitted_at"].startswith("2026-01-15")
    assert created["decision_at"] is None
    assert created["created_at"]
    assert created["updated_at"]

    items = await _list(api, headers)
    assert [row["id"] for row in items] == [created["id"]]

    patched = await api.patch(
        f"/api/v1/applications/{created['id']}",
        json={"status": "offer", "decision_at": "2026-03-01T12:00:00Z", "url": None},
        headers=headers,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["status"] == "offer"
    assert patched.json()["decision_at"].startswith("2026-03-01")
    assert patched.json()["url"] is None  # explicit null clears the field
    # Untouched fields survive a partial update.
    assert patched.json()["program_name"] == "M.Sc. Robotics"
    assert patched.json()["notes"] == "Translated transcript sent"

    removed = await api.delete(f"/api/v1/applications/{created['id']}", headers=headers)
    assert removed.status_code == 200, removed.text
    assert removed.json() == {"id": created["id"], "deleted": True}

    assert await _list(api, headers) == []


async def test_patch_updates_only_the_fields_sent(api: AsyncClient) -> None:
    headers = await _register(api, "app-partial")
    created = await _create(api, headers, notes="first draft notes")

    patched = await api.patch(
        f"/api/v1/applications/{created['id']}", json={"status": "interview"}, headers=headers
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["status"] == "interview"
    assert body["university"] == "TU Munich"
    assert body["notes"] == "first draft notes"
    # updated_at is maintained by the applications_updated_at trigger.
    assert body["updated_at"] >= body["created_at"]


async def test_list_filters_by_status(api: AsyncClient) -> None:
    headers = await _register(api, "app-filter")
    draft = await _create(api, headers, university="TU Berlin")
    submitted = await _create(api, headers, university="UvA", status="submitted")

    assert {row["id"] for row in await _list(api, headers)} == {draft["id"], submitted["id"]}
    only_submitted = await _list(api, headers, status="submitted")
    assert [row["id"] for row in only_submitted] == [submitted["id"]]
    assert await _list(api, headers, status="waitlist") == []


# ------------------------------------------------------------ validation


async def test_create_rejects_an_unknown_status(api: AsyncClient) -> None:
    headers = await _register(api, "app-badstatus")
    response = await api.post(
        "/api/v1/applications",
        json={"university": "TU Munich", "status": "maybe"},
        headers=headers,
    )
    assert response.status_code == 422, response.text
    error = _error(response)
    assert error["code"] == "VALIDATION_ERROR"


async def test_create_requires_university_and_status(api: AsyncClient) -> None:
    headers = await _register(api, "app-missing")
    empty = await api.post("/api/v1/applications", json={}, headers=headers)
    assert empty.status_code == 422, empty.text
    assert _error(empty)["code"] == "VALIDATION_ERROR"

    no_status = await api.post(
        "/api/v1/applications", json={"university": "TU Munich"}, headers=headers
    )
    assert no_status.status_code == 422, no_status.text
    assert _error(no_status)["code"] == "VALIDATION_ERROR"


async def test_blank_university_is_a_422(api: AsyncClient) -> None:
    """Whitespace-only input is a missing field, not a university name."""
    headers = await _register(api, "app-blank")
    response = await api.post(
        "/api/v1/applications",
        json={"university": "   ", "status": "draft"},
        headers=headers,
    )
    assert response.status_code == 422, response.text
    error = _error(response)
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"].get("field") == "university"


async def test_unknown_status_filter_is_a_422(api: AsyncClient) -> None:
    """A typo'd filter must not read as "no applications in that stage"."""
    headers = await _register(api, "app-badfilter")
    response = await api.get(
        "/api/v1/applications", params={"status": "banana"}, headers=headers
    )
    assert response.status_code == 422, response.text
    assert _error(response)["code"] == "VALIDATION_ERROR"


# ------------------------------------------------- row-level security


async def test_foreign_row_is_404_on_read_and_mutate(api: AsyncClient) -> None:
    """User B must learn nothing about user A's rows: not that they exist
    (list stays empty), not their contents (404, never 403), and B's
    PATCH/DELETE attempts leave A's row untouched."""
    owner = await _register(api, "app-owner")
    stranger = await _register(api, "app-stranger")
    mine = await _create(api, owner, university="ETH Zurich", status="submitted")

    # Read: A's row never shows up in B's list.
    assert await _list(api, stranger) == []

    # Mutate: foreign id behaves exactly like a missing one (404).
    patched = await api.patch(
        f"/api/v1/applications/{mine['id']}", json={"status": "rejected"}, headers=stranger
    )
    assert patched.status_code == 404, patched.text
    error = _error(patched)
    assert error["code"] == "NOT_FOUND"

    removed = await api.delete(f"/api/v1/applications/{mine['id']}", headers=stranger)
    assert removed.status_code == 404, removed.text
    assert _error(removed)["code"] == "NOT_FOUND"

    # The owner's row still exists, unchanged, after both attempts.
    owner_items = await _list(api, owner)
    assert [row["id"] for row in owner_items] == [mine["id"]]
    assert owner_items[0]["status"] == "submitted"
    assert owner_items[0]["university"] == "ETH Zurich"


async def test_mutating_an_unknown_id_is_404(api: AsyncClient) -> None:
    """A never-existing id and a foreign id are the same response."""
    headers = await _register(api, "app-ghost")
    ghost = str(uuid.uuid4())

    patched = await api.patch(
        f"/api/v1/applications/{ghost}", json={"status": "offer"}, headers=headers
    )
    assert patched.status_code == 404, patched.text
    assert _error(patched)["code"] == "NOT_FOUND"

    removed = await api.delete(f"/api/v1/applications/{ghost}", headers=headers)
    assert removed.status_code == 404, removed.text
    assert _error(removed)["code"] == "NOT_FOUND"


async def test_delete_leaves_the_row_readable_by_nobody(api: AsyncClient) -> None:
    headers = await _register(api, "app-gone")
    created = await _create(api, headers)
    first = await api.delete(f"/api/v1/applications/{created['id']}", headers=headers)
    assert first.status_code == 200, first.text

    again = await api.delete(f"/api/v1/applications/{created['id']}", headers=headers)
    assert again.status_code == 404, again.text
    assert _error(again)["code"] == "NOT_FOUND"
