"""P1-9 (typed validation) + P2-15 (document file upload) over HTTP.

Integration tests against real PostgreSQL (fixture: tests/conftest.py).

Assertions follow the frontend contract in `frontend/app/lib/api-extra.ts`:
* a missing/unknown `document_type` or `status` is a 422 VALIDATION_ERROR
  envelope (never a 500);
* 404s carry `error.code = "NOT_FOUND"` so `isMissingResource` fires while
  `isRouteUnavailable` (404 with a null code) does not;
* the 10 MB cap answers 413 FILE_TOO_LARGE with the same sentence the UI
  shows the student.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

_CHECKLIST = {
    "transcript",
    "passport",
    "language_score",
    "cv",
    "sop",
    "lors",
    "portfolio",
    "financial_proof",
}
_PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


@pytest.fixture
async def api(db_session: Any) -> AsyncIterator[AsyncClient]:
    from app import main as main_module
    from app.main import app

    main_module._rate_counters.clear()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


@pytest.fixture(autouse=True)
def isolated_upload_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Never write uploads into the repo: point the storage root at tmp_path."""
    from app.services.documents import storage

    monkeypatch.setattr(storage, "upload_root", lambda: tmp_path)
    return tmp_path


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


async def _create(api: AsyncClient, headers: dict[str, str], doc_type: str) -> dict[str, Any]:
    response = await api.post(
        "/api/v1/documents", json={"document_type": doc_type}, headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()


def _error(response: Any) -> dict[str, Any]:
    body = response.json()
    assert "error" in body, body
    return body["error"]


def _stored_files(root: Path) -> list[Path]:
    """Files currently on disk under the (sync) upload root."""
    return [path for path in root.rglob("*") if path.is_file()]


# ------------------------------------------------------------------ P1-9


async def test_list_returns_the_whole_checklist(api: AsyncClient) -> None:
    headers = await _register(api, "doc-list")
    response = await api.get("/api/v1/documents", headers=headers)
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert {i["document_type"] for i in items} == _CHECKLIST
    for item in items:
        assert set(item) == {"id", "document_type", "status", "expires_at"}
        assert item["status"] == "TODO"


async def test_create_normalizes_the_document_type(api: AsyncClient) -> None:
    headers = await _register(api, "doc-norm")
    created = await _create(api, headers, "  Language   Score ")
    assert created["document_type"] == "language_score"
    assert created["status"] == "TODO"
    uuid.UUID(created["id"])


async def test_create_rejects_an_unknown_document_type(api: AsyncClient) -> None:
    headers = await _register(api, "doc-badtype")
    response = await api.post(
        "/api/v1/documents", json={"document_type": "banana"}, headers=headers
    )
    assert response.status_code == 422, response.text
    error = _error(response)
    assert error["code"] == "VALIDATION_ERROR"
    assert "document_type" in error["message"]
    assert error["details"].get("field") == "document_type"


async def test_create_rejects_a_missing_document_type(api: AsyncClient) -> None:
    """P1-9: the old dict body raised a KeyError -> 500."""
    headers = await _register(api, "doc-missing")
    response = await api.post("/api/v1/documents", json={}, headers=headers)
    assert response.status_code == 422, response.text
    assert _error(response)["code"] == "VALIDATION_ERROR"


async def test_patch_validates_the_status(api: AsyncClient) -> None:
    headers = await _register(api, "doc-patch")
    doc = await _create(api, headers, "cv")

    bad = await api.patch(
        f"/api/v1/documents/{doc['id']}", json={"status": "MAYBE"}, headers=headers
    )
    assert bad.status_code == 422, bad.text
    assert _error(bad)["code"] == "VALIDATION_ERROR"

    good = await api.patch(
        f"/api/v1/documents/{doc['id']}",
        json={"status": "IN_PROGRESS", "notes": "Waiting on HR"},
        headers=headers,
    )
    assert good.status_code == 200, good.text
    assert good.json() == {"id": doc["id"], "status": "IN_PROGRESS"}


async def test_other_users_document_is_a_404_not_a_403(api: AsyncClient) -> None:
    owner = await _register(api, "doc-owner")
    stranger = await _register(api, "doc-stranger")
    doc = await _create(api, owner, "sop")

    patched = await api.patch(
        f"/api/v1/documents/{doc['id']}", json={"status": "DONE"}, headers=stranger
    )
    assert patched.status_code == 404, patched.text
    assert _error(patched)["code"] == "NOT_FOUND"

    downloaded = await api.get(f"/api/v1/documents/{doc['id']}/file", headers=stranger)
    assert downloaded.status_code == 404, downloaded.text
    assert _error(downloaded)["code"] == "NOT_FOUND"


# ----------------------------------------------------------------- P2-15


async def test_upload_download_delete_roundtrip(
    api: AsyncClient, isolated_upload_dir: Path
) -> None:
    headers = await _register(api, "doc-file")
    doc = await _create(api, headers, "transcript")

    response = await api.post(
        f"/api/v1/documents/{doc['id']}/file",
        files={"file": ("transcript.pdf", _PDF_BYTES, "application/pdf")},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == {
        "id",
        "document_type",
        "status",
        "file_name",
        "file_size",
        "has_file",
    }
    assert payload["file_name"] == "transcript.pdf"
    assert payload["file_size"] == len(_PDF_BYTES)
    assert payload["has_file"] is True
    assert _stored_files(isolated_upload_dir), "the bytes must land on disk"

    download = await api.get(f"/api/v1/documents/{doc['id']}/file", headers=headers)
    assert download.status_code == 200, download.text
    assert download.content == _PDF_BYTES
    assert download.headers["content-type"].startswith("application/pdf")

    removed = await api.delete(f"/api/v1/documents/{doc['id']}/file", headers=headers)
    assert removed.status_code == 200, removed.text
    assert removed.json() == {"id": doc["id"], "has_file": False}
    assert _stored_files(isolated_upload_dir) == []


async def test_upload_alias_route_is_equivalent(api: AsyncClient) -> None:
    """PLAN.md spells the route /upload; the frontend falls back to it."""
    headers = await _register(api, "doc-alias")
    doc = await _create(api, headers, "passport")
    response = await api.post(
        f"/api/v1/documents/{doc['id']}/upload",
        files={"file": ("passport.png", b"\x89PNG\r\n\x1a\n", "image/png")},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["has_file"] is True
    assert response.json()["file_name"] == "passport.png"


async def test_upload_replaces_the_previous_file(api: AsyncClient) -> None:
    headers = await _register(api, "doc-replace")
    doc = await _create(api, headers, "cv")
    for name in ("cv-old.pdf", "cv-final.pdf"):
        response = await api.post(
            f"/api/v1/documents/{doc['id']}/file",
            files={"file": (name, _PDF_BYTES, "application/pdf")},
            headers=headers,
        )
        assert response.status_code == 200, response.text
    assert response.json()["file_name"] == "cv-final.pdf"
    assert response.json()["has_file"] is True


async def test_upload_rejects_an_unsupported_type(api: AsyncClient) -> None:
    headers = await _register(api, "doc-badext")
    doc = await _create(api, headers, "portfolio")
    response = await api.post(
        f"/api/v1/documents/{doc['id']}/file",
        files={"file": ("malware.exe", b"MZ", "application/octet-stream")},
        headers=headers,
    )
    assert response.status_code == 422, response.text
    error = _error(response)
    assert error["code"] == "VALIDATION_ERROR"
    assert "PDF, PNG, JPG or DOCX" in error["message"]


async def test_upload_rejects_an_empty_file(api: AsyncClient) -> None:
    headers = await _register(api, "doc-empty")
    doc = await _create(api, headers, "lors")
    response = await api.post(
        f"/api/v1/documents/{doc['id']}/file",
        files={"file": ("empty.pdf", b"", "application/pdf")},
        headers=headers,
    )
    assert response.status_code == 422, response.text
    assert _error(response)["code"] == "VALIDATION_ERROR"


async def test_upload_rejects_a_file_over_ten_megabytes(api: AsyncClient) -> None:
    headers = await _register(api, "doc-huge")
    doc = await _create(api, headers, "financial_proof")
    too_big = b"x" * (10 * 1024 * 1024 + 1)
    response = await api.post(
        f"/api/v1/documents/{doc['id']}/file",
        files={"file": ("bank-statement.pdf", too_big, "application/pdf")},
        headers=headers,
    )
    assert response.status_code == 413, response.text
    error = _error(response)
    assert error["code"] == "FILE_TOO_LARGE"
    assert "10 MB" in error["message"]


async def test_download_without_a_file_is_a_404_envelope(api: AsyncClient) -> None:
    """`isRouteUnavailable` treats 404 + code:null as "route not deployed" —
    the envelope's NOT_FOUND is what keeps the UI honest."""
    headers = await _register(api, "doc-nofile")
    doc = await _create(api, headers, "transcript")
    response = await api.get(f"/api/v1/documents/{doc['id']}/file", headers=headers)
    assert response.status_code == 404, response.text
    error = _error(response)
    assert error["code"] == "NOT_FOUND"
    assert error["message"]


async def test_delete_without_a_file_is_a_404_envelope(api: AsyncClient) -> None:
    """The dashboard shows "There was no file to remove." for this 404."""
    headers = await _register(api, "doc-delnone")
    doc = await _create(api, headers, "transcript")
    response = await api.delete(f"/api/v1/documents/{doc['id']}/file", headers=headers)
    assert response.status_code == 404, response.text
    error = _error(response)
    assert error["code"] == "NOT_FOUND"
    assert "no file" in error["message"].lower()


async def test_upload_of_an_unknown_document_is_a_404(api: AsyncClient) -> None:
    headers = await _register(api, "doc-ghost")
    response = await api.post(
        f"/api/v1/documents/{uuid.uuid4()}/file",
        files={"file": ("x.pdf", _PDF_BYTES, "application/pdf")},
        headers=headers,
    )
    assert response.status_code == 404, response.text
    assert _error(response)["code"] == "NOT_FOUND"
