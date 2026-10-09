"""Application tracker endpoints (W12).

Contract (PLAN.md W12-W15):
    GET    /applications            -> {items:[...]}   ?status= optional filter
    POST   /applications            {university, program_name?, status, url?,
                                     notes?, submitted_at?, decision_at?}
    PATCH  /applications/{id}       partial update of the fields sent
    DELETE /applications/{id}       -> {id, deleted}

The router is pre-registered in app/main.py — fill in routes here; do not
edit main.py. Every handler resolves the current user through the same
ContextVar-backed resolver the monitor/documents routes use (the auth
middleware decodes the bearer token once per request; anonymous callers
fall back to the local demo account), and rows outside that user are a 404
— never a 403 — so cross-user probing stays blind.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApplicationStatus
from app.db.session import get_session
from app.schemas.applications import (
    ApplicationCreate,
    ApplicationDeletedOut,
    ApplicationListOut,
    ApplicationOut,
    ApplicationPatch,
)
from app.services import applications as applications_service
from app.services.profile import get_or_create_default_user

router = APIRouter(tags=["applications"])


@router.get("/applications", response_model=ApplicationListOut)
async def list_applications(
    status: ApplicationStatus | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> ApplicationListOut:
    """The signed-in student's applications, optionally narrowed by status.

    An unknown ``status`` is a 422 VALIDATION_ERROR (FastAPI validates the
    query against the enum) — never a silently empty list that would look
    like "you have no offers" when the caller simply typo'd the filter.
    """
    user = await get_or_create_default_user(session)
    rows = await applications_service.list_applications(session, user.id, status=status)
    return ApplicationListOut(items=[ApplicationOut.model_validate(row) for row in rows])


@router.post("/applications", response_model=ApplicationOut)
async def create_application(
    payload: ApplicationCreate, session: AsyncSession = Depends(get_session)
) -> ApplicationOut:
    user = await get_or_create_default_user(session)
    row = await applications_service.create_application(session, user.id, payload)
    return ApplicationOut.model_validate(row)


@router.patch("/applications/{application_id}", response_model=ApplicationOut)
async def update_application(
    application_id: uuid.UUID,
    payload: ApplicationPatch,
    session: AsyncSession = Depends(get_session),
) -> ApplicationOut:
    user = await get_or_create_default_user(session)
    row = await applications_service.update_application(
        session, user.id, application_id, payload
    )
    return ApplicationOut.model_validate(row)


@router.delete("/applications/{application_id}", response_model=ApplicationDeletedOut)
async def delete_application(
    application_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> ApplicationDeletedOut:
    user = await get_or_create_default_user(session)
    deleted_id = await applications_service.delete_application(
        session, user.id, application_id
    )
    return ApplicationDeletedOut(id=deleted_id, deleted=True)
