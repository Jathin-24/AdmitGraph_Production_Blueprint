"""Application tracker request/response schemas (W12).

Status values are pinned by the API contract (PLAN.md W12): a wrong or
missing ``status`` must come back as a 422 VALIDATION_ERROR envelope from
the shared handler. Plain ``Literal`` types do that (same approach as the
documents router's DocumentPatchInput, mirroring TaskStatus); custom
validators would not — their ``ValueError`` lands in ``errors()["ctx"]``,
which the envelope cannot serialise, turning a friendly 422 into a 500.

The literals below mirror ``app.db.models.ApplicationStatus`` on purpose:
the model enum types the column, the schemas type the wire format.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

ApplicationStatusLiteral = Literal[
    "draft",
    "submitted",
    "interview",
    "offer",
    "rejected",
    "waitlist",
    "withdrawn",
]


class ApplicationCreate(BaseModel):
    """POST /applications body.

    ``university`` and ``status`` are required (the contract lists them
    without ``?``); everything else is optional. Whitespace-only
    ``university`` is rejected in the service, not here — see
    ``app.services.applications`` for why normalization stays out of
    validators.
    """

    model_config = ConfigDict(extra="ignore")

    university: str
    program_name: str | None = None
    status: ApplicationStatusLiteral
    url: str | None = None
    notes: str | None = None
    submitted_at: datetime | None = None
    decision_at: datetime | None = None


class ApplicationPatch(BaseModel):
    """PATCH /applications/{id} body: only the fields actually sent are
    applied (the router checks ``model_fields_set``, so an explicit null
    clears a nullable column while an absent field is left untouched)."""

    model_config = ConfigDict(extra="ignore")

    university: str | None = None
    program_name: str | None = None
    status: ApplicationStatusLiteral | None = None
    url: str | None = None
    notes: str | None = None
    submitted_at: datetime | None = None
    decision_at: datetime | None = None


class ApplicationOut(BaseModel):
    """One tracker row as the frontend consumes it (mirrors the model)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    university: str
    program_name: str | None
    status: ApplicationStatusLiteral
    url: str | None
    notes: str | None
    submitted_at: datetime | None
    decision_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ApplicationListOut(BaseModel):
    items: list[ApplicationOut]


class ApplicationDeletedOut(BaseModel):
    id: UUID
    deleted: bool = True
