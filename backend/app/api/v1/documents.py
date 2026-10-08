import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.models import Document, TaskStatus
from app.db.session import get_session
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


def _norm_doc_type(value: str) -> str:
    return value.strip().lower().replace(" ", "_")


async def _owned_document(session: AsyncSession, document_id: uuid.UUID) -> Document:
    """Load a document that belongs to the caller's profile, else 404.

    The 404 (not 403) keeps cross-user probing blind: another user's document
    id is indistinguishable from a non-existent one.
    """
    profile = await get_or_create_profile(session)
    doc = await session.get(Document, document_id)
    if doc is None or doc.profile_id != profile.id:
        raise AppError(404, "NOT_FOUND", "Document not found")
    return doc


@router.get("/documents")
async def list_documents(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    profile = await get_or_create_profile(session)
    query = (
        select(Document).where(Document.profile_id == profile.id).order_by(Document.created_at)
    )
    rows = list((await session.execute(query)).scalars().all())

    # Lazily provision any missing checklist items for this profile so the
    # readiness panel always reflects the full set of application documents.
    present = {_norm_doc_type(d.document_type) for d in rows}
    missing = [t for t in READINESS_CHECKLIST if t not in present]
    if missing:
        for doc_type in missing:
            session.add(
                Document(profile_id=profile.id, document_type=doc_type, status=TaskStatus.TODO)
            )
        await session.commit()
        rows = list((await session.execute(query)).scalars().all())

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
    payload: dict[str, Any], session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    profile = await get_or_create_profile(session)
    doc = Document(
        profile_id=profile.id, document_type=str(payload["document_type"]), notes=payload.get("notes")
    )
    session.add(doc)
    await session.commit()
    return {"id": str(doc.id), "document_type": doc.document_type, "status": doc.status.value}


@router.patch("/documents/{document_id}")
async def update_document(
    document_id: uuid.UUID, payload: dict[str, Any], session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    doc = await _owned_document(session, document_id)
    if "status" in payload:
        doc.status = TaskStatus(payload["status"])
    if "notes" in payload:
        doc.notes = payload["notes"]
    await session.commit()
    return {"id": str(doc.id), "status": doc.status.value}
