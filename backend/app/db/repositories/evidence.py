"""Evidence listing, conflict, member and service-layer queries.

Backs the ``app.api.v1.evidence`` router (the router keeps the conflict
resolution rules — membership, already-resolved, authority preference) and the
``app.services.evidence`` pipeline (the services keep extraction, sync,
recheck and access *rules*; every statement they run lives here).
"""

import uuid
from collections.abc import Collection
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ApplicationPlan,
    Country,
    EligibilityAssessment,
    EligibilityEvidence,
    Evidence,
    EvidenceConflict,
    EvidenceConflictMember,
    FitAssessment,
    FitDimensionEvidence,
    Institution,
    Intake,
    MonitorSubscription,
    Program,
    Requirement,
    RequirementVersion,
    Risk,
    RiskEvidence,
    RoadmapTask,
    SavedProgram,
    SearchResult,
    SearchRun,
    Source,
    SourceAuthority,
    StudentProfile,
    User,
)

# Transaction control for the evidence router (conflict resolution).
from app.db.repositories.base import commit as commit
from app.db.repositories.base import refresh as refresh


async def list_evidence(
    session: AsyncSession, program_id: uuid.UUID | None
) -> list[tuple[Evidence, Source | None]]:
    query = (
        select(Evidence, Source)
        .join(Source, Evidence.source_id == Source.id, isouter=True)
        .order_by(Evidence.retrieved_at.desc())
        .limit(50)
    )
    if program_id is not None:
        query = query.where(Evidence.subject_type == "program", Evidence.subject_id == program_id)
    rows = (await session.execute(query)).all()
    return [(evidence, source) for evidence, source in rows]


async def get_evidence(session: AsyncSession, evidence_id: uuid.UUID) -> Evidence | None:
    return await session.get(Evidence, evidence_id)


async def list_conflicts(
    session: AsyncSession, evidence_id: uuid.UUID
) -> list[EvidenceConflict]:
    result = await session.execute(
        select(EvidenceConflict)
        .join(EvidenceConflictMember, EvidenceConflictMember.conflict_id == EvidenceConflict.id)
        .where(EvidenceConflictMember.evidence_id == evidence_id)
    )
    return list(result.scalars().all())


async def get_conflict(session: AsyncSession, conflict_id: uuid.UUID) -> EvidenceConflict | None:
    return await session.get(EvidenceConflict, conflict_id)


async def list_conflict_members(session: AsyncSession, conflict_id: uuid.UUID) -> list[Evidence]:
    members = (
        await session.execute(
            select(Evidence)
            .join(
                EvidenceConflictMember,
                EvidenceConflictMember.evidence_id == Evidence.id,
            )
            .where(EvidenceConflictMember.conflict_id == conflict_id)
        )
    ).scalars().all()
    return list(members)


# ------------------------------------- requirement sync (services/evidence)


async def known_claim_evidence(
    session: AsyncSession, subject_type: str, keys: Collection[str]
) -> list[Evidence]:
    """Evidence rows carrying known claim keys for one subject type."""
    rows = (
        await session.execute(
            select(Evidence).where(
                Evidence.subject_type == subject_type,
                Evidence.subject_id.is_not(None),
                Evidence.normalized_claim.in_(keys),
            )
        )
    ).scalars().all()
    return list(rows)


async def existing_program_ids(session: AsyncSession, program_ids: set[uuid.UUID]) -> set[uuid.UUID]:
    """Which of ``program_ids`` actually have a programs row (FK-safe sync)."""
    rows = (
        await session.execute(select(Program.id).where(Program.id.in_(program_ids)))
    ).scalars()
    return set(rows)


async def source_authorities(
    session: AsyncSession, source_ids: set[uuid.UUID]
) -> dict[uuid.UUID, SourceAuthority]:
    rows = (
        await session.execute(
            select(Source.id, Source.source_authority).where(Source.id.in_(source_ids))
        )
    ).all()
    return {source_id: authority for source_id, authority in rows}


async def requirement_for_key(
    session: AsyncSession, program_id: uuid.UUID, normalized_key: str
) -> Requirement | None:
    row = (
        await session.execute(
            select(Requirement).where(
                Requirement.program_id == program_id,
                Requirement.normalized_key == normalized_key,
            )
        )
    ).scalar_one_or_none()
    return row


async def open_requirement_version(
    session: AsyncSession, requirement_id: uuid.UUID
) -> RequirementVersion | None:
    """The newest version whose ``valid_to`` is still open, if any."""
    row = (
        await session.execute(
            select(RequirementVersion)
            .where(
                RequirementVersion.requirement_id == requirement_id,
                RequirementVersion.valid_to.is_(None),
            )
            .order_by(RequirementVersion.valid_from.desc())
            .limit(1)
        )
    ).scalars().first()
    return row


# -------------------------------------- extraction/recheck (services/evidence)


async def source_by_canonical_url(session: AsyncSession, canonical_url: str) -> Source | None:
    row = (
        await session.execute(select(Source).where(Source.canonical_url == canonical_url))
    ).scalar_one_or_none()
    return row


async def sources_by_ids(session: AsyncSession, source_ids: set[uuid.UUID]) -> dict[uuid.UUID, Source]:
    rows = (
        await session.execute(select(Source).where(Source.id.in_(source_ids)))
    ).scalars().all()
    return {s.id: s for s in rows}


async def evidence_for_subject(
    session: AsyncSession, subject_type: str, subject_id: uuid.UUID
) -> list[Evidence]:
    rows = (
        await session.execute(
            select(Evidence).where(
                Evidence.subject_type == subject_type, Evidence.subject_id == subject_id
            )
        )
    ).scalars().all()
    return list(rows)


async def unresolved_conflict(
    session: AsyncSession, conflict_key: str
) -> EvidenceConflict | None:
    """The UNRESOLVED conflict group for one key, if any (idempotent detection)."""
    row = (
        await session.execute(
            select(EvidenceConflict).where(
                EvidenceConflict.conflict_key == conflict_key,
                EvidenceConflict.resolution_status == "UNRESOLVED",
            )
        )
    ).scalar_one_or_none()
    return row


async def conflict_member(
    session: AsyncSession, conflict_id: uuid.UUID, evidence_id: uuid.UUID
) -> EvidenceConflictMember | None:
    row = (
        await session.execute(
            select(EvidenceConflictMember).where(
                EvidenceConflictMember.conflict_id == conflict_id,
                EvidenceConflictMember.evidence_id == evidence_id,
            )
        )
    ).scalar_one_or_none()
    return row


async def conflict_member_evidence_ids(
    session: AsyncSession, conflict_id: uuid.UUID
) -> list[uuid.UUID]:
    rows = (
        await session.execute(
            select(EvidenceConflictMember.evidence_id).where(
                EvidenceConflictMember.conflict_id == conflict_id
            )
        )
    ).scalars()
    return list(rows)


async def programs_by_normalized_name(
    session: AsyncSession, normalized_name: str, limit: int = 2
) -> list[Program]:
    rows = (
        await session.execute(
            select(Program).where(Program.normalized_name == normalized_name).limit(limit)
        )
    ).scalars().all()
    return list(rows)


async def programs_by_institution_domains(
    session: AsyncSession, domains: list[str]
) -> list[Program]:
    """Programs behind catalog institutions owning one of ``domains`` (capped:
    several programs behind one domain are ambiguous)."""
    rows = (
        await session.execute(
            select(Program)
            .join(Institution, Institution.id == Program.institution_id)
            .where(Institution.domain.in_(domains))
            .limit(2)
        )
    ).scalars().all()
    return list(rows)


async def signal_result_rows(
    session: AsyncSession,
    *,
    purposes: tuple[str, ...],
    engines: tuple[str, ...],
    search_run_ids: list[uuid.UUID] | None = None,
    limit: int = 20,
) -> list[tuple[SearchResult, Source, SearchRun]]:
    """(result, source, run) rows for a signal purpose — bounded, newest first."""
    filters: list[Any] = []
    if purposes:
        filters.append(SearchRun.parameters["purpose"].astext.in_(purposes))
    if engines:
        filters.append(SearchRun.engine.in_(engines))
    if not filters:
        return []
    stmt = (
        select(SearchResult, Source, SearchRun)
        .join(SearchRun, SearchResult.search_run_id == SearchRun.id)
        .join(Source, SearchResult.source_id == Source.id, isouter=True)
        .where(or_(*filters))
        .order_by(SearchRun.requested_at.desc(), SearchResult.position.asc().nulls_last())
        .limit(limit)
    )
    if search_run_ids:
        stmt = stmt.where(SearchRun.id.in_(search_run_ids))
    rows = (await session.execute(stmt)).all()
    return [(result, source, run) for result, source, run in rows]


async def reobservation_candidates(
    session: AsyncSession, normalized_claim: str, search_result_id: uuid.UUID | None
) -> list[Evidence]:
    """Evidence rows for the same claim key (and search result), if any."""
    stmt = select(Evidence).where(Evidence.normalized_claim == normalized_claim)
    if search_result_id is None:
        stmt = stmt.where(Evidence.search_result_id.is_(None))
    else:
        stmt = stmt.where(Evidence.search_result_id == search_result_id)
    rows = (await session.execute(stmt)).scalars().all()
    return list(rows)


async def stored_country_code(session: AsyncSession, code: str) -> str | None:
    """The country code as stored, or None when it is not in `countries`."""
    row = (await session.execute(select(Country.code).where(Country.code == code))).first()
    return row[0] if row is not None else None


async def intake_for_program_year(
    session: AsyncSession, program_id: uuid.UUID, intake_year: int
) -> Intake | None:
    row = (
        await session.execute(
            select(Intake).where(
                Intake.program_id == program_id, Intake.intake_year == intake_year
            )
        )
    ).scalars().first()
    return row


# ------------------------------------ access scoping (services/evidence)


async def risk_evidence_profile_ids(session: AsyncSession, evidence_id: uuid.UUID) -> set[uuid.UUID]:
    rows = await session.execute(
        select(Risk.profile_id)
        .join(RiskEvidence, RiskEvidence.risk_id == Risk.id)
        .where(RiskEvidence.evidence_id == evidence_id)
    )
    return {pid for pid in rows.scalars() if pid is not None}


async def fit_dimension_evidence_profile_ids(
    session: AsyncSession, evidence_id: uuid.UUID
) -> set[uuid.UUID]:
    rows = await session.execute(
        select(FitAssessment.profile_id)
        .join(FitDimensionEvidence, FitDimensionEvidence.fit_assessment_id == FitAssessment.id)
        .where(FitDimensionEvidence.evidence_id == evidence_id)
    )
    return {pid for pid in rows.scalars() if pid is not None}


async def eligibility_evidence_profile_ids(
    session: AsyncSession, evidence_id: uuid.UUID
) -> set[uuid.UUID]:
    rows = await session.execute(
        select(FitAssessment.profile_id)
        .join(
            EligibilityAssessment,
            EligibilityAssessment.fit_assessment_id == FitAssessment.id,
        )
        .join(
            EligibilityEvidence,
            EligibilityEvidence.eligibility_assessment_id == EligibilityAssessment.id,
        )
        .where(EligibilityEvidence.evidence_id == evidence_id)
    )
    return {pid for pid in rows.scalars() if pid is not None}


async def roadmap_task_profile_ids(session: AsyncSession, evidence_id: uuid.UUID) -> set[uuid.UUID]:
    rows = await session.execute(
        select(RoadmapTask.profile_id).where(
            RoadmapTask.evidence_ids.contains([str(evidence_id)])
        )
    )
    return {pid for pid in rows.scalars() if pid is not None}


async def saved_program_profile_ids(
    session: AsyncSession, program_id: uuid.UUID
) -> set[uuid.UUID]:
    rows = await session.execute(
        select(SavedProgram.profile_id).where(SavedProgram.program_id == program_id)
    )
    return {pid for pid in rows.scalars() if pid is not None}


async def application_plan_profile_ids(
    session: AsyncSession, program_id: uuid.UUID
) -> set[uuid.UUID]:
    rows = await session.execute(
        select(ApplicationPlan.profile_id).where(ApplicationPlan.program_id == program_id)
    )
    return {pid for pid in rows.scalars() if pid is not None}


async def fit_program_profile_ids(
    session: AsyncSession, program_id: uuid.UUID
) -> set[uuid.UUID]:
    rows = await session.execute(
        select(FitAssessment.profile_id).where(FitAssessment.program_id == program_id)
    )
    return {pid for pid in rows.scalars() if pid is not None}


async def monitor_subscription_profile_ids(
    session: AsyncSession, program_id: uuid.UUID
) -> set[uuid.UUID]:
    rows = await session.execute(
        select(MonitorSubscription.profile_id).where(MonitorSubscription.program_id == program_id)
    )
    return {pid for pid in rows.scalars() if pid is not None}


async def risk_program_profile_ids(
    session: AsyncSession, program_id: uuid.UUID
) -> set[uuid.UUID]:
    rows = await session.execute(
        select(Risk.profile_id).where(Risk.program_id == program_id)
    )
    return {pid for pid in rows.scalars() if pid is not None}


async def profile_emails(session: AsyncSession, profile_ids: set[uuid.UUID]) -> list[str]:
    """Emails of the accounts owning ``profile_ids`` (demo/shared-corpus rule)."""
    rows = await session.execute(
        select(User.email)
        .join(StudentProfile, StudentProfile.user_id == User.id)
        .where(StudentProfile.id.in_(profile_ids))
    )
    return [email for (email,) in rows.all()]
