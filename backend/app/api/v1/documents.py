"""Document checklist + uploaded-file endpoints (P1-9, P2-15).

Contract (API_CONTRACT + PLAN.md cross-WS notes):
    GET    /documents                      -> {items:[{id, document_type, status, expires_at}]}
    POST   /documents                      {document_type, notes?} -> 200 {id, document_type, status}
    PATCH  /documents/{id}                 {status?, notes?}       -> 200 {id, status}
    POST   /documents/{id}/file            multipart "file"        -> 200 {id, ...file meta}
    POST   /documents/{id}/upload          alias of /file (PLAN spelling)
    GET    /documents/{id}/file            -> the stored bytes (404 when none)
    DELETE /documents/{id}/file            -> 200 (404 when no file)

P1-9 hardening: the create/patch bodies are typed models instead of raw dicts
so a missing ``document_type`` or an unknown ``status`` is a 422
VALIDATION_ERROR envelope instead of a KeyError/ValueError 500, and
``document_type`` is normalized then checked against the eight readiness
checklist keys.

P2-15 storage rules live in ``app.services.documents.storage`` (extension
allowlist, sane content types, 10 MB cap, uuid-named files under the upload
root scoped to one directory per document id).

Ownership: every route 404s (never 403s) for documents outside the caller's
profile, so cross-user probing stays blind.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.events import emit
from app.db.models import Document, TaskStatus
from app.db.repositories import documents as documents_repo
from app.db.session import get_session
from app.services.documents import storage
from app.services.profile import get_or_create_profile

router = APIRouter(tags=["documents"])

# The standard application-readiness checklist (MASTER_SPEC §17). These rows
# are task placeholders (TODO), not factual claims about any program.
READINESS_CHECKLIST = (
    "transcript",
    "passport",
    "language_score",
    "cv",
    "sop",
    "lors",
    "portfolio",
    "financial_proof",
)

NO_FILE_MESSAGE = "No file has been uploaded for this document"


def _norm_doc_type(value: str) -> str:
    """Case/space-insensitive form of a checklist key (""SOP" -> "sop")."""
    collapsed = re.sub(r"\s+", " ", value.strip())
    return collapsed.lower().replace(" ", "_")


class DocumentCreateInput(BaseModel):
    """Typed create body (P1-9): the router answers 422 VALIDATION_ERROR
    instead of a 500 when ``document_type`` is missing or not a checklist key.

    Validation lives in :func:`_normalize_document_type` rather than in a
    Pydantic ``field_validator`` on purpose: a validator's ``ValueError`` is
    carried in ``errors()["ctx"]``, which the shared 422 envelope cannot
    serialise — that would turn a friendly 422 back into a 500.
    """

    model_config = ConfigDict(extra="ignore")

    document_type: str | None = None
    notes: str | None = None


class DocumentPatchInput(BaseModel):
    """Typed patch body: only the fields actually sent are applied."""

    model_config = ConfigDict(extra="ignore")

    status: Literal["TODO", "IN_PROGRESS", "DONE", "BLOCKED", "SKIPPED"] | None = None
    notes: str | None = None


def _normalize_document_type(raw: str | None) -> str:
    """Normalize then whitelist; raises 422 VALIDATION_ERROR otherwise."""
    if raw is None or not raw.strip():
        raise AppError(422, "VALIDATION_ERROR", "document_type is required")
    normalized = _norm_doc_type(raw)
    if normalized not in READINESS_CHECKLIST:
        allowed = ", ".join(READINESS_CHECKLIST)
        raise AppError(
            422,
            "VALIDATION_ERROR",
            f"Unknown document_type — expected one of: {allowed}",
            details={"field": "document_type"},
        )
    return normalized


async def _owned_document(session: AsyncSession, document_id: uuid.UUID) -> Document:
    """Load a document that belongs to the caller's profile, else 404.

    The 404 (not 403) keeps cross-user probing blind: another user's document
    id is indistinguishable from a non-existent one.
    """
    profile = await get_or_create_profile(session)
    doc = await documents_repo.get_document(session, document_id)
    if doc is None or doc.profile_id != profile.id:
        raise AppError(404, "NOT_FOUND", "Document not found")
    return doc


def _document_payload(doc: Document) -> dict[str, Any]:
    return {"id": str(doc.id), "document_type": doc.document_type, "status": doc.status.value}


@router.get("/documents")
async def list_documents(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    profile = await get_or_create_profile(session)
    rows = await documents_repo.list_documents(session, profile.id)

    # Lazily provision any missing checklist items for this profile so the
    # readiness panel always reflects the full set of application documents.
    present = {_norm_doc_type(d.document_type) for d in rows}
    missing = [t for t in READINESS_CHECKLIST if t not in present]
    if missing:
        await documents_repo.add_documents(
            session,
            [
                Document(profile_id=profile.id, document_type=doc_type, status=TaskStatus.TODO)
                for doc_type in missing
            ],
        )
        rows = await documents_repo.list_documents(session, profile.id)

    return {
        "items": [
            {
                "id": str(d.id),
                "document_type": d.document_type,
                "status": d.status.value,
                "expires_at": str(d.expires_at) if d.expires_at else None,
            }
            for d in rows
        ]
    }


@router.post("/documents")
async def create_document(
    payload: DocumentCreateInput, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    profile = await get_or_create_profile(session)
    doc = Document(
        profile_id=profile.id,
        document_type=_normalize_document_type(payload.document_type),
        notes=payload.notes,
        status=TaskStatus.TODO,
    )
    await documents_repo.add_documents(session, [doc])
    emit("document.created", document_id=str(doc.id), document_type=doc.document_type)
    return _document_payload(doc)


@router.patch("/documents/{document_id}")
async def update_document(
    document_id: uuid.UUID,
    payload: DocumentPatchInput,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    doc = await _owned_document(session, document_id)
    if "status" in payload.model_fields_set and payload.status is not None:
        doc.status = TaskStatus(payload.status)
    if "notes" in payload.model_fields_set:
        doc.notes = payload.notes
    await documents_repo.commit(session)
    return {"id": str(doc.id), "status": doc.status.value}


# --- P2-15: uploaded files ---------------------------------------------------


async def _store_upload(doc: Document, file: UploadFile, session: AsyncSession) -> Document:
    """Validate, persist and record an uploaded file (replaces any previous)."""
    ext = storage.validate(file.filename, file.content_type)
    data = await storage.read_upload(file)
    if doc.file_path:
        storage.delete_stored(doc.file_path)  # superseded file
    original = Path(file.filename or "").name or f"document{ext}"
    doc.file_path = storage.store_bytes(doc.id, data, ext)
    doc.file_name = original
    doc.file_size = len(data)
    await documents_repo.commit(session)
    emit(
        "document.file_uploaded",
        document_id=str(doc.id),
        file_name=doc.file_name,
        file_size=doc.file_size,
    )
    return doc


def _file_payload(doc: Document) -> dict[str, Any]:
    return {
        **_document_payload(doc),
        "file_name": doc.file_name,
        "file_size": doc.file_size,
        "has_file": doc.file_path is not None,
    }


async def _upload_route(
    document_id: uuid.UUID,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    doc = await _owned_document(session, document_id)
    doc = await _store_upload(doc, file, session)
    return _file_payload(doc)


@router.post("/documents/{document_id}/file")
async def upload_document_file(
    document_id: uuid.UUID,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Store the document's file (≤10 MB, pdf/png/jpg/jpeg/docx)."""
    return await _upload_route(document_id, file, session)


@router.post("/documents/{document_id}/upload")
async def upload_document_file_alias(
    document_id: uuid.UUID,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """PLAN.md spelling of POST /documents/{id}/file (frontend tries both)."""
    return await _upload_route(document_id, file, session)


@router.get("/documents/{document_id}/file")
async def download_document_file(
    document_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> FileResponse:
    doc = await _owned_document(session, document_id)
    if not doc.file_path:
        raise AppError(404, "NOT_FOUND", NO_FILE_MESSAGE)
    path = storage.resolve_stored(doc.file_path)
    if not path.is_file():
        raise AppError(404, "NOT_FOUND", NO_FILE_MESSAGE)
    media_type = storage.ALLOWED_TYPES.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media_type, filename=doc.file_name or path.name)


@router.delete("/documents/{document_id}/file")
async def delete_document_file(
    document_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    doc = await _owned_document(session, document_id)
    if not doc.file_path:
        raise AppError(404, "NOT_FOUND", NO_FILE_MESSAGE)
    storage.delete_stored(doc.file_path)
    doc.file_path = None
    doc.file_name = None
    doc.file_size = None
    await documents_repo.commit(session)
    emit("document.file_removed", document_id=str(doc.id))
    return {"id": str(doc.id), "has_file": False}
