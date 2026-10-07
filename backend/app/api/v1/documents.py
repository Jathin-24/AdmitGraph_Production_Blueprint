import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document, TaskStatus
from app.db.session import get_session
from app.services.profile import get_or_create_profile

router = APIRouter(tags=["documents"])


@router.get("/documents")
async def list_documents(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    result = await session.execute(select(Document))
    return {
        "items": [
            {
                "id": str(d.id),
                "document_type": d.document_type,
                "status": d.status.value,
                "expires_at": str(d.expires_at) if d.expires_at else None,
            }
            for d in result.scalars().all()
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
    doc = await session.get(Document, document_id)
    if doc is None:
        return {"error": "Not found"}
    if "status" in payload:
        doc.status = TaskStatus(payload["status"])
    if "notes" in payload:
        doc.notes = payload["notes"]
    await session.commit()
    return {"id": str(doc.id), "status": doc.status.value}
