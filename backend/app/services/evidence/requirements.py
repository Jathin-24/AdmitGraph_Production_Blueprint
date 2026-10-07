"""Sync Requirement rows from stored evidence. Deterministic, evidence-first."""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Evidence, Program, Requirement, RequirementStatus
from app.services.evidence.llm_extract import KEY_OPERATOR, KEY_TO_REQUIREMENT_TYPE, KNOWN_CLAIM_KEYS

logger = logging.getLogger(__name__)


def requirement_title(normalized_key: str) -> str:
    return normalized_key.replace("_", " ").title()


async def sync_requirements_from_evidence(
    session: AsyncSession, subject_type: str = "program"
) -> dict[str, int]:
    """Upserts requirements for every program with known-key evidence."""
    rows = (
        await session.execute(
            select(Evidence).where(
                Evidence.subject_type == subject_type,
                Evidence.subject_id.is_not(None),
                Evidence.normalized_claim.in_(KNOWN_CLAIM_KEYS),
            )
        )
    ).scalars().all()

    # Requirements have a FK to programs: skip evidence whose subject no longer
    # (or never) exists rather than crashing the caller with an FK violation.
    subject_ids = {r.subject_id for r in rows if r.subject_id is not None}
    known_program_ids: set[UUID] = set()
    if subject_ids:
        known_program_ids = set(
            (
                await session.execute(
                    select(Program.id).where(Program.id.in_(subject_ids))
                )
            ).scalars()
        )

    created = 0
    updated = 0
    seen: set[tuple[UUID, str]] = set()
    for row in rows:
        if row.subject_id is None or not row.normalized_claim:
            continue
        if row.subject_id not in known_program_ids:
            logger.debug("skipping evidence for unknown program subject %s", row.subject_id)
            continue
        key = (row.subject_id, row.normalized_claim)
        if key in seen:
            continue
        seen.add(key)

        existing = (
            await session.execute(
                select(Requirement).where(
                    Requirement.program_id == row.subject_id,
                    Requirement.normalized_key == row.normalized_claim,
                )
            )
        ).scalar_one_or_none()

        value = dict(row.extracted_value or {})
        if not value:
            continue

        if existing is None:
            session.add(
                Requirement(
                    program_id=row.subject_id,
                    requirement_type=KEY_TO_REQUIREMENT_TYPE.get(row.normalized_claim, "other"),
                    title=requirement_title(row.normalized_claim),
                    normalized_key=row.normalized_claim,
                    operator=KEY_OPERATOR.get(row.normalized_claim, "eq"),
                    value=value,
                    mandatory=row.normalized_claim in ("ielts_overall_min", "academic_cgpa_min"),
                    status=RequirementStatus.UNKNOWN,
                    last_verified_at=row.retrieved_at,
                )
            )
            created += 1
        else:
            existing.value = value
            existing.last_verified_at = row.retrieved_at
            updated += 1

    await session.commit()
    return {"requirements_created": created, "requirements_updated": updated}
