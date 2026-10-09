"""Strategy pipeline data access (research → fit → risk → build steps).

Every SQL statement ``app.services.strategy.persist`` runs lives here: the
pipeline steps keep their orchestration and business rules (evaluation,
scoring, risk assessment, portfolio/roadmap shaping) and hand the raw reads
and deletes to these functions. Writes stay in the service (session.add /
flush around its own commit points) — only statements go through the
repository layer.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ApplicationPlan,
    Document,
    EducationRecord,
    EducationSubject,
    EligibilityAssessment,
    EligibilityEvidence,
    Evidence,
    EvidenceConflict,
    EvidenceConflictMember,
    EvidenceStatus,
    FitAssessment,
    Intake,
    Program,
    Requirement,
    RequirementStatus,
    Risk,
    RiskStatus,
    TestScore,
)

_SEVERITY_RANK: dict[str, int] = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


# ------------------------------------------------------------- documents/facts


async def list_profile_documents(session: AsyncSession, profile_id: uuid.UUID) -> list[Document]:
    rows = (
        await session.execute(select(Document).where(Document.profile_id == profile_id))
    ).scalars().all()
    return list(rows)


async def latest_english_test(
    session: AsyncSession, profile_id: uuid.UUID, test_types: Collection[str]
) -> TestScore | None:
    """Most recent English test on record (onboarding's own type vocabulary).

    Test rows of any other kind (GRE, GMAT, ...) never stand in for an English
    result, and a score without an expiry date keeps expiry UNKNOWN.
    """
    return (
        (
            await session.execute(
                select(TestScore)
                .where(
                    TestScore.profile_id == profile_id,
                    TestScore.test_type.in_(test_types),
                )
                .order_by(TestScore.test_date.desc().nullslast(), TestScore.created_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )


async def education_subject_rows(
    session: AsyncSession, profile_id: uuid.UUID
) -> list[tuple[str, str | None, Decimal | None]]:
    """(subject_name, normalized_subject, credits) for the profile's education."""
    rows = (
        await session.execute(
            select(
                EducationSubject.subject_name,
                EducationSubject.normalized_subject,
                EducationSubject.credits,
            )
            .join(EducationRecord, EducationSubject.education_record_id == EducationRecord.id)
            .where(EducationRecord.profile_id == profile_id)
        )
    ).all()
    return [(name, normalized, credits) for name, normalized, credits in rows]


# --------------------------------------------------- program scoping/requirements


async def fit_program_ids(session: AsyncSession, profile_id: uuid.UUID) -> set[uuid.UUID]:
    fit_ids = (
        await session.execute(
            select(FitAssessment.program_id).where(FitAssessment.profile_id == profile_id)
        )
    ).scalars()
    return set(fit_ids)


async def plan_program_ids(session: AsyncSession, profile_id: uuid.UUID) -> set[uuid.UUID]:
    ids = (
        await session.execute(
            select(ApplicationPlan.program_id).where(ApplicationPlan.profile_id == profile_id)
        )
    ).scalars()
    return set(ids)


async def researched_programs(session: AsyncSession) -> list[Program]:
    """Programs the run actually researched: those with extracted requirements.

    Fit and eligibility can only be honestly computed where requirements exist.
    A program with no extracted facts must not outrank one whose status is
    known — "no data" is uncertainty, not a better position.
    """
    return list(
        (
            await session.execute(
                select(Program)
                .join(Requirement, Requirement.program_id == Program.id)
                .distinct()
                .order_by(Program.canonical_name)
            )
        )
        .scalars()
        .all()
    )


async def program_requirements(session: AsyncSession, program_id: uuid.UUID) -> list[Requirement]:
    result = (
        await session.execute(select(Requirement).where(Requirement.program_id == program_id))
    ).scalars().all()
    return list(result)


async def requirements_for_programs(
    session: AsyncSession, program_ids: set[uuid.UUID]
) -> list[Requirement]:
    result = (
        await session.execute(select(Requirement).where(Requirement.program_id.in_(program_ids)))
    ).scalars().all()
    return list(result)


async def mandatory_problem_requirements(
    session: AsyncSession, program_ids: set[uuid.UUID]
) -> list[Requirement]:
    """Mandatory requirements that are NOT_SATISFIED or CONFLICTING (roadmap)."""
    result = (
        await session.execute(
            select(Requirement)
            .where(Requirement.program_id.in_(program_ids))
            .where(Requirement.mandatory.is_(True))
            .where(
                Requirement.status.in_(
                    (RequirementStatus.NOT_SATISFIED, RequirementStatus.CONFLICTING)
                )
            )
            .order_by(Requirement.normalized_key)
        )
    ).scalars().all()
    return list(result)


async def unsatisfied_mandatory_requirement_ids(
    session: AsyncSession, program_ids: set[uuid.UUID]
) -> list[uuid.UUID]:
    rows = (
        await session.execute(
            select(Requirement.id).where(
                Requirement.program_id.in_(program_ids),
                Requirement.mandatory.is_(True),
                Requirement.status == RequirementStatus.NOT_SATISFIED,
            )
        )
    ).all()
    return [row[0] for row in rows]


# ----------------------------------------------------------------- intakes


async def future_intake_deadlines(
    session: AsyncSession, program_ids: set[uuid.UUID], today: date
) -> list[date]:
    """Application deadlines still in the future across the given programs."""
    if not program_ids:
        return []
    rows = (
        await session.execute(
            select(Intake.application_deadline).where(
                Intake.program_id.in_(program_ids),
                Intake.application_deadline.is_not(None),
                Intake.application_deadline >= today,
            )
        )
    ).scalars().all()
    return [d for d in rows if d is not None]


async def all_intake_deadlines(
    session: AsyncSession, program_ids: set[uuid.UUID]
) -> list[date]:
    """Every known application deadline across the given programs (past included)."""
    if not program_ids:
        return []
    rows = (
        await session.execute(
            select(Intake.application_deadline).where(
                Intake.program_id.in_(program_ids),
                Intake.application_deadline.is_not(None),
            )
        )
    ).scalars().all()
    return [d for d in rows if d is not None]


async def program_deadline_rows(
    session: AsyncSession, program_ids: set[uuid.UUID]
) -> list[tuple[uuid.UUID, date]]:
    """(program id, deadline) rows for every extracted deadline (past included)."""
    if not program_ids:
        return []
    rows = (
        await session.execute(
            select(Intake.program_id, Intake.application_deadline).where(
                Intake.program_id.in_(program_ids),
                Intake.application_deadline.is_not(None),
            )
        )
    ).all()
    return [(program_id, deadline) for program_id, deadline in rows if deadline is not None]


async def nearest_future_intake_rows(
    session: AsyncSession, program_ids: set[uuid.UUID], today: date
) -> list[tuple[uuid.UUID, date]]:
    """(program id, deadline) rows for intakes still open (future deadlines)."""
    if not program_ids:
        return []
    rows = (
        await session.execute(
            select(Intake.program_id, Intake.application_deadline)
            .where(Intake.program_id.in_(program_ids))
            .where(Intake.application_deadline.is_not(None))
            .where(Intake.application_deadline >= today)
        )
    ).all()
    return [(program_id, deadline) for program_id, deadline in rows if deadline is not None]


async def future_intakes(
    session: AsyncSession, program_ids: set[uuid.UUID], today: date
) -> list[Intake]:
    """Full intake rows still open, earliest deadline first (roadmap tasks)."""
    if not program_ids:
        return []
    intake_rows = list(
        (
            await session.execute(
                select(Intake)
                .where(Intake.program_id.in_(program_ids))
                .where(Intake.application_deadline.is_not(None))
                .where(Intake.application_deadline >= today)
                .order_by(Intake.application_deadline.asc())
            )
        )
        .scalars()
        .all()
    )
    return intake_rows


async def intake_at_deadline(
    session: AsyncSession, program_ids: set[uuid.UUID], deadline: date | None
) -> Intake | None:
    """The intake carrying one exact deadline (its evidence provenance)."""
    if not program_ids or deadline is None:
        return None
    intake = (
        await session.execute(
            select(Intake).where(
                Intake.program_id.in_(program_ids),
                Intake.application_deadline == deadline,
            )
        )
    ).scalars().first()
    return intake


# ------------------------------------------------------------------ evidence


async def conflicting_claim_keys(
    session: AsyncSession, program_ids: set[uuid.UUID]
) -> dict[uuid.UUID, set[str]]:
    """Program -> normalized keys whose evidence is CONFLICTING."""
    if not program_ids:
        return {}
    rows = (
        await session.execute(
            select(Evidence.normalized_claim, Evidence.subject_id).where(
                Evidence.subject_type == "program",
                Evidence.subject_id.in_(program_ids),
                Evidence.status == EvidenceStatus.CONFLICTING,
                Evidence.normalized_claim.is_not(None),
            )
        )
    ).all()
    out: dict[uuid.UUID, set[str]] = {}
    for key, subject_id in rows:
        if subject_id is None or key is None:
            continue
        out.setdefault(subject_id, set()).add(str(key))
    return out


async def evidence_keys_by_program(
    session: AsyncSession, program_ids: set[uuid.UUID]
) -> dict[tuple[uuid.UUID, str], list[uuid.UUID]]:
    """(program, claim key) -> CONFLICTING evidence ids (eligibility links)."""
    if not program_ids:
        return {}
    rows = (
        await session.execute(
            select(Evidence.subject_id, Evidence.normalized_claim, Evidence.id).where(
                Evidence.subject_type == "program",
                Evidence.subject_id.in_(program_ids),
                Evidence.status == EvidenceStatus.CONFLICTING,
                Evidence.normalized_claim.is_not(None),
            )
        )
    ).all()
    out: dict[tuple[uuid.UUID, str], list[uuid.UUID]] = {}
    for subject_id, key, evidence_id in rows:
        if subject_id is None or key is None:
            continue
        out.setdefault((subject_id, str(key)), []).append(evidence_id)
    return out


async def all_evidence_keys_by_program(
    session: AsyncSession, program_ids: set[uuid.UUID]
) -> dict[tuple[uuid.UUID, str], list[uuid.UUID]]:
    """(program, claim key) -> evidence ids, unfiltered (roadmap evidence links)."""
    if not program_ids:
        return {}
    rows = (
        await session.execute(
            select(Evidence.subject_id, Evidence.normalized_claim, Evidence.id).where(
                Evidence.subject_type == "program",
                Evidence.subject_id.in_(program_ids),
                Evidence.normalized_claim.is_not(None),
            )
        )
    ).all()
    out: dict[tuple[uuid.UUID, str], list[uuid.UUID]] = {}
    for subject_id, key, evidence_id in rows:
        if subject_id is None or key is None:
            continue
        out.setdefault((subject_id, str(key)), []).append(evidence_id)
    return out


async def latest_fits_by_program(
    session: AsyncSession, profile_id: uuid.UUID
) -> dict[uuid.UUID, FitAssessment]:
    """Newest fit per program for this profile (created_at is server-set, so a
    tie falls back to insertion order)."""
    rows = (
        await session.execute(
            select(FitAssessment)
            .where(FitAssessment.profile_id == profile_id)
            .order_by(FitAssessment.created_at.desc())
        )
    ).scalars().all()
    out: dict[uuid.UUID, FitAssessment] = {}
    for fit in rows:
        out.setdefault(fit.program_id, fit)
    return out


async def strategy_fits(
    session: AsyncSession, profile_id: uuid.UUID, research_plan_id: uuid.UUID | None
) -> list[FitAssessment]:
    """Fits for this profile, best score first.

    A plan id scopes to that run's assessments (earlier runs keep their own
    strategies); ``None`` keeps every fit — a plan-less rebuild across runs.
    """
    fit_query = select(FitAssessment).where(FitAssessment.profile_id == profile_id)
    if research_plan_id is not None:
        # Only this run's assessments; earlier runs keep their own strategies.
        fit_query = fit_query.where(FitAssessment.research_plan_id == research_plan_id)
    rows = (
        await session.execute(fit_query.order_by(FitAssessment.overall_score.desc()))
    ).scalars().all()
    return list(rows)


async def eligibility_assessments_by_fit_ids(
    session: AsyncSession, fit_ids: list[uuid.UUID]
) -> list[EligibilityAssessment]:
    rows = (
        await session.execute(
            select(EligibilityAssessment).where(EligibilityAssessment.fit_assessment_id.in_(fit_ids))
        )
    ).scalars().all()
    return list(rows)


async def eligibility_evidence_links(
    session: AsyncSession, assessment_ids: list[uuid.UUID]
) -> list[EligibilityEvidence]:
    rows = (
        await session.execute(
            select(EligibilityEvidence).where(
                EligibilityEvidence.eligibility_assessment_id.in_(assessment_ids)
            )
        )
    ).scalars().all()
    return list(rows)


async def portfolio_tuition_amounts(
    session: AsyncSession, program_ids: set[uuid.UUID], currency: str | None
) -> list[Decimal | None]:
    """In-currency tuition amounts across the portfolio (no FX is invented)."""
    rows = (
        await session.execute(
            select(Program.tuition_amount).where(
                Program.id.in_(program_ids),
                Program.tuition_amount.is_not(None),
                Program.tuition_currency == currency,
            )
        )
    ).scalars().all()
    return list(rows)


async def stale_evidence_ids(
    session: AsyncSession, program_ids: set[uuid.UUID], now: datetime
) -> list[uuid.UUID]:
    if not program_ids:
        return []
    rows = (
        await session.execute(
            select(Evidence.id).where(
                Evidence.subject_type == "program",
                Evidence.subject_id.in_(program_ids),
                (Evidence.status == EvidenceStatus.STALE)
                | ((Evidence.freshness_deadline.is_not(None)) & (Evidence.freshness_deadline < now)),
            )
        )
    ).all()
    return [r[0] for r in rows]


async def claim_evidence_ids(
    session: AsyncSession, program_ids: set[uuid.UUID], keys: set[str]
) -> list[uuid.UUID]:
    if not program_ids:
        return []
    rows = (
        await session.execute(
            select(Evidence.id).where(
                Evidence.subject_type == "program",
                Evidence.subject_id.in_(program_ids),
                Evidence.normalized_claim.in_(keys),
            )
        )
    ).all()
    return [r[0] for r in rows]


async def evidence_ids_by_key(
    session: AsyncSession, program_ids: set[uuid.UUID]
) -> dict[str, list[uuid.UUID]]:
    if not program_ids:
        return {}
    rows = (
        await session.execute(
            select(Evidence.normalized_claim, Evidence.id).where(
                Evidence.subject_type == "program",
                Evidence.subject_id.in_(program_ids),
                Evidence.normalized_claim.is_not(None),
            )
        )
    ).all()
    out: dict[str, list[uuid.UUID]] = {}
    for key, evidence_id in rows:
        out.setdefault(str(key), []).append(evidence_id)
    return out


async def unresolved_conflict_rows(
    session: AsyncSession, program_ids: set[uuid.UUID]
) -> list[tuple[EvidenceConflict, uuid.UUID]]:
    """(conflict, member evidence id) rows for UNRESOLVED conflicts."""
    if not program_ids:
        return []
    rows = (
        await session.execute(
            select(EvidenceConflict, EvidenceConflictMember.evidence_id)
            .join(
                EvidenceConflictMember,
                EvidenceConflictMember.conflict_id == EvidenceConflict.id,
            )
            .join(Evidence, Evidence.id == EvidenceConflictMember.evidence_id)
            .where(
                EvidenceConflict.resolution_status == "UNRESOLVED",
                Evidence.subject_type == "program",
                Evidence.subject_id.in_(program_ids),
            )
        )
    ).all()
    return [(conflict, evidence_id) for conflict, evidence_id in rows]


async def programs_without_career_evidence(
    session: AsyncSession, program_ids: set[uuid.UUID]
) -> list[tuple[uuid.UUID, str]]:
    """(program id, program name) for portfolio programs that carry no
    claim_type="career" evidence — names come from the stored Program rows."""
    if not program_ids:
        return []
    names = {
        p.id: p.canonical_name
        for p in (await session.execute(select(Program).where(Program.id.in_(program_ids)))).scalars()
    }
    with_career = {
        subject_id
        for subject_id in (
            await session.execute(
                select(Evidence.subject_id).where(
                    Evidence.subject_type == "program",
                    Evidence.subject_id.in_(program_ids),
                    Evidence.claim_type == "career",
                )
            )
        ).scalars()
        if subject_id is not None
    }
    return sorted(
        ((pid, names[pid]) for pid in program_ids if pid in names and pid not in with_career),
        key=lambda item: (item[1], str(item[0])),
    )


# -------------------------------------------------------------------- risks


async def portfolio_risk_stats(
    session: AsyncSession, profile_id: uuid.UUID
) -> dict[uuid.UUID, tuple[int, str]]:
    """OPEN risks grouped by program: (count, worst severity).

    Only program-scoped risks count: a portfolio-level risk (budget, documents,
    freshness) says nothing about which program is safer, so it must not tilt
    every candidate's rank or force a broaden on its own.
    """
    rows = (
        await session.execute(
            select(Risk.program_id, Risk.severity).where(
                Risk.profile_id == profile_id,
                Risk.status == RiskStatus.OPEN,
                Risk.program_id.is_not(None),
            )
        )
    ).all()
    counts: dict[uuid.UUID, int] = {}
    worst: dict[uuid.UUID, str] = {}
    for program_id, severity in rows:
        if program_id is None:
            continue
        counts[program_id] = counts.get(program_id, 0) + 1
        value = severity.value
        current = worst.get(program_id)
        if current is None or _SEVERITY_RANK.get(value, 9) < _SEVERITY_RANK.get(current, 9):
            worst[program_id] = value
    return {pid: (counts[pid], worst[pid]) for pid in counts}


async def open_risk_ids(session: AsyncSession, profile_id: uuid.UUID) -> list[uuid.UUID]:
    open_ids = [
        row[0]
        for row in (
            await session.execute(
                select(Risk.id).where(
                    Risk.profile_id == profile_id, Risk.status == RiskStatus.OPEN
                )
            )
        ).all()
    ]
    return open_ids


async def delete_risks(session: AsyncSession, risk_ids: list[uuid.UUID]) -> None:
    await session.execute(delete(Risk).where(Risk.id.in_(risk_ids)))


# ------------------------------------------------------------------ programs


async def programs_by_ids(
    session: AsyncSession, program_ids: set[uuid.UUID]
) -> dict[uuid.UUID, Program]:
    rows = (
        await session.execute(select(Program).where(Program.id.in_(program_ids)))
    ).scalars()
    return {p.id: p for p in rows}
