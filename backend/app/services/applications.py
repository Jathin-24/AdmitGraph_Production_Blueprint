"""Application tracker business logic (W12).

Follows the monitor/documents split: the router keeps HTTP shaping (status
codes, payload serialisation) while this module owns the queries, the
field normalization and the ownership decision. Statements are built with
the SQLAlchemy ORM only — no raw SQL.

Ownership: every lookup filters on ``user_id`` inside the query itself, so
another user's row comes back as ``None`` and is rendered as a 404 — never
a 403 — which keeps cross-user probing blind (indistinguishable from a
row that never existed).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.models import Application, ApplicationStatus
from app.schemas.applications import ApplicationCreate, ApplicationPatch


def _clean_university(raw: str | None) -> str:
    """Trimmed university label, or a 422 when nothing usable is left.

    Normalization lives here rather than in a Pydantic validator on
    purpose: a validator's ``ValueError`` is carried in ``errors()["ctx"]``,
    which the shared 422 envelope cannot serialise — that would turn a
    friendly 422 back into a 500 (same reasoning as the documents router).
    """
    value = (raw or "").strip()
    if not value:
        raise AppError(
            422,
            "VALIDATION_ERROR",
            "university is required",
            details={"field": "university"},
        )
    return value


def _clean_optional(raw: str | None) -> str | None:
    """Trim a free-text optional field; blank becomes NULL, never ""."""
    value = (raw or "").strip()
    return value or None


async def list_applications(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    status: ApplicationStatus | None = None,
) -> list[Application]:
    """The caller's rows, newest first, optionally filtered by status."""
    stmt = (
        select(Application)
        .where(Application.user_id == user_id)
        .order_by(Application.created_at.desc(), Application.id)
    )
    if status is not None:
        stmt = stmt.where(Application.status == status)
    return list((await session.execute(stmt)).scalars().all())


async def get_owned_application(
    session: AsyncSession, user_id: uuid.UUID, application_id: uuid.UUID
) -> Application:
    """Load one of the caller's rows, else 404.

    Scoping happens in the WHERE clause (not by loading then comparing):
    a foreign id and a missing id take the exact same code path, so the
    response cannot reveal that somebody else's application exists.
    """
    row: Application | None = (
        await session.execute(
            select(Application).where(
                Application.id == application_id,
                Application.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise AppError(404, "NOT_FOUND", "Application not found")
    return row


async def create_application(
    session: AsyncSession, user_id: uuid.UUID, payload: ApplicationCreate
) -> Application:
    row = Application(
        user_id=user_id,
        university=_clean_university(payload.university),
        program_name=_clean_optional(payload.program_name),
        status=ApplicationStatus(payload.status),
        url=_clean_optional(payload.url),
        notes=payload.notes,  # kept verbatim: the student's own words
        submitted_at=payload.submitted_at,
        decision_at=payload.decision_at,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)  # server-generated id/timestamps
    return row


async def update_application(
    session: AsyncSession,
    user_id: uuid.UUID,
    application_id: uuid.UUID,
    payload: ApplicationPatch,
) -> Application:
    """Apply only the fields the client actually sent (partial update)."""
    row = await get_owned_application(session, user_id, application_id)
    sent = payload.model_fields_set
    if "university" in sent:
        row.university = _clean_university(payload.university)
    if "program_name" in sent:
        row.program_name = _clean_optional(payload.program_name)
    if "status" in sent and payload.status is not None:
        # An explicit null cannot be stored (the column is NOT NULL) and
        # must not silently keep the old value either — it is ignored the
        # same way the documents router ignores it.
        row.status = ApplicationStatus(payload.status)
    if "url" in sent:
        row.url = _clean_optional(payload.url)
    if "notes" in sent:
        row.notes = payload.notes
    if "submitted_at" in sent:
        row.submitted_at = payload.submitted_at
    if "decision_at" in sent:
        row.decision_at = payload.decision_at
    await session.commit()
    await session.refresh(row)
    return row


async def delete_application(
    session: AsyncSession, user_id: uuid.UUID, application_id: uuid.UUID
) -> uuid.UUID:
    """Remove one of the caller's rows; 404 for anything not theirs."""
    row = await get_owned_application(session, user_id, application_id)
    deleted_id = row.id
    await session.delete(row)
    await session.commit()
    return deleted_id
