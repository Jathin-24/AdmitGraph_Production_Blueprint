"""Document checklist queries.

Backs the ``app.api.v1.documents`` router; the router keeps the readiness
checklist provisioning logic and the profile-scoping 404 decision.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document

# Transaction control for the documents router (PATCH /documents/{id}).
from app.db.repositories.base import commit as commit


async def get_document(session: AsyncSession, document_id: uuid.UUID) -> Document | None:
    return await session.get(Document, document_id)


async def list_documents(session: AsyncSession, profile_id: uuid.UUID) -> list[Document]:
    query = select(Document).where(Document.profile_id == profile_id).order_by(Document.created_at)
    rows = (await session.execute(query)).scalars().all()
    return list(rows)


async def add_documents(session: AsyncSession, documents: Sequence[Document]) -> None:
    for doc in documents:
        session.add(doc)
    await session.commit()
