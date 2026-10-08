"""Persistence helpers for strategy pipeline steps.

Inputs here are REAL data (requirements, evidence, conflicts, intakes,
documents, tuition, budget) — nothing in this module hard-codes a health score
or a risk. Formulas are documented next to their helpers.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ApplicationPlan,
    Document,
    Evidence,
    EvidenceConflictMember,
    EvidenceStatus,
    FitAssessment,
    Intake,
    Program,
    Requirement,
    RequirementStatus,
    Risk,
    RiskEvidence,
    RiskStatus,
    RoadmapTask,
    RunStatus,
    StrategyRun,
    TaskStatus,
)
from app.services.matching.evaluator import ProfileFacts, evaluate
from app.services.risk.engine import RiskContext, assess
from app.services.scoring.scorer import SCORING_VERSION, DimensionInput, overall_score
from app.services.strategy.health import plan_health_score
from app.services.strategy.portfolio import Candidate, build_portfolio

STRATEGY_VERSION = "v1"

# MASTER_SPEC §17 Application Readiness: the core documents every applicant
# must have ready (CV/SOP/letters are tracked too but are not "core" here).
CORE_DOCUMENT_TYPES = ("transcript", "passport", "language_score", "financial_proof")
_DOCUMENT_TYPE_ALIASES = {
    "language": "language_score",
    "language_test": "language_score",
    "ielts": "language_score",
    "toefl": "language_score",
    "english_score": "language_score",
    "financial": "financial_proof",
    "financial_statement": "financial_proof",
    "bank_statement": "financial_proof",
    "proof_of_funds": "financial_proof",
    "marksheet": "transcript",
    "academic_transcript": "transcript",
}


def _normalize_doc_type(raw: str) -> str:
    key = raw.strip().lower().replace(" ", "_").replace("-", "_")
    return _DOCUMENT_TYPE_ALIASES.get(key, key)


async def documents_ready(session: AsyncSession, profile_id: uuid.UUID) -> bool:
    """True only when all core document types exist with status DONE."""
    rows = (
        await session.execute(select(Document).where(Document.profile_id == profile_id))
    ).scalars().all()
    by_type: dict[str, TaskStatus] = {}
    for row in rows:
        by_type[_normalize_doc_type(row.document_type)] = row.status
    return all(by_type.get(t) == TaskStatus.DONE for t in CORE_DOCUMENT_TYPES)


async def _scope_program_ids(session: AsyncSession, profile_id: uuid.UUID) -> set[uuid.UUID]:
    """Programs in play for this profile: scored fits + programs with requirements."""
    fit_ids = (
        await session.execute(
            select(FitAssessment.program_id).where(FitAssessment.profile_id == profile_id)
        )
    ).scalars()
    req_ids = (await session.execute(select(Requirement.program_id))).scalars()
    return set(fit_ids) | set(req_ids)


async def _researched_programs(session: AsyncSession) -> list[Program]:
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


async def _future_deadline(
    session: AsyncSession, program_ids: set[uuid.UUID]
) -> date | None:
    """Earliest FUTURE application deadline across the given programs."""
    if not program_ids:
        return None
    today = date.today()
    rows = (
        await session.execute(
            select(Intake.application_deadline).where(
                Intake.program_id.in_(program_ids),
                Intake.application_deadline.is_not(None),
                Intake.application_deadline >= today,
            )
        )
    ).scalars().all()
    deadlines = [d for d in rows if d is not None]
    return min(deadlines) if deadlines else None


async def plan_health_inputs(
    session: AsyncSession, profile_id: uuid.UUID, program_ids: set[uuid.UUID]
) -> dict[str, Any]:
    """Real inputs for plan_health_score (MASTER_SPEC §17 Plan Health).

    - blocker_count: mandatory requirements NOT_SATISFIED across the portfolio
    - unresolved_conflicts: Evidence rows with status CONFLICTING on portfolio programs
    - stale_evidence_count: portfolio Evidence past its freshness window (STALE)
    - nearest_deadline: earliest future Intake.application_deadline in the portfolio
    - financial_feasible: in-currency tuition comparison (see _financial_feasible)
    - documents_ready: every core document type tracked with status DONE
    """
    now = datetime.now(UTC)
    blockers = 0
    conflicts = 0
    stale = 0
    if program_ids:
        blockers = len(
            (
                await session.execute(
                    select(Requirement.id).where(
                        Requirement.program_id.in_(program_ids),
                        Requirement.mandatory.is_(True),
                        Requirement.status == RequirementStatus.NOT_SATISFIED,
                    )
                )
            ).all()
        )
        evidence_rows = (
            await session.execute(
                select(Evidence).where(
                    Evidence.subject_type == "program", Evidence.subject_id.in_(program_ids)
                )
            )
        ).scalars().all()
        conflicts = sum(1 for e in evidence_rows if e.status == EvidenceStatus.CONFLICTING)
        stale = sum(
            1
            for e in evidence_rows
            if e.status == EvidenceStatus.STALE
            or (e.freshness_deadline is not None and e.freshness_deadline < now)
        )
    return {
        "blocker_count": blockers,
        "unresolved_conflicts": conflicts,
        "stale_evidence_count": stale,
        "nearest_deadline": await _future_deadline(session, program_ids),
        "financial_feasible": await _financial_feasible(session, profile_id, program_ids),
        "documents_ready": await documents_ready(session, profile_id),
    }


async def _financial_feasible(
    session: AsyncSession, profile_id: uuid.UUID, program_ids: set[uuid.UUID]
) -> bool:
    """Tuition budget vs portfolio tuition comparison.

    Formula (no FX conversion is invented):
      budget = profile.tuition_budget_amount or total_budget_amount (budget_currency)
      comparable = portfolio programs whose tuition_amount is set AND whose
                   tuition_currency == budget_currency
      feasible   = every comparable tuition <= budget
    Programs with unknown tuition or a foreign currency are UNKNOWN: unknown is
    not infeasible, it simply does not reduce feasibility. When no budget or no
    comparable tuition exists, feasibility is UNKNOWN -> True (no penalty).
    """
    from app.db.models import StudentProfile

    profile = await session.get(StudentProfile, profile_id)
    if profile is None:
        return True
    budget = profile.tuition_budget_amount or profile.total_budget_amount
    if budget is None or not program_ids:
        return True
    tuition_rows = (
        await session.execute(
            select(Program.tuition_amount).where(
                Program.id.in_(program_ids),
                Program.tuition_amount.is_not(None),
                Program.tuition_currency == profile.budget_currency,
            )
        )
    ).scalars().all()
    if not tuition_rows:
        return True  # UNKNOWN: no comparable tuition figure
    amounts = [a for a in tuition_rows if a is not None]
    return all(amount <= budget for amount in amounts)


async def step_evaluate_requirements(session: AsyncSession, profile_id: uuid.UUID) -> dict[str, Any]:
    programs = await _researched_programs(session)
    conflicting_keys = await _conflicting_claim_keys(session, {p.id for p in programs})
    evaluated = 0
    for program in programs:
        reqs = (
            await session.execute(select(Requirement).where(Requirement.program_id == program.id))
        ).scalars().all()
        facts = await _profile_facts(session, profile_id)
        for req in reqs:
            status, reason = evaluate(
                {
                    "normalized_key": req.normalized_key,
                    "operator": req.operator or "",
                    "value": req.value,
                },
                facts,
            )
            # Conflicting evidence on this program+key wins: never silently
            # choose a value (MASTER_SPEC §11). Surfaced as CONFLICTING in the
            # requirements API and as a SOURCE_CONFLICT risk.
            if req.normalized_key in conflicting_keys.get(program.id, set()):
                status = RequirementStatus.CONFLICTING
            req.status = status
            evaluated += 1
    await session.commit()
    return {"requirements_evaluated": evaluated}


async def _conflicting_claim_keys(
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


async def step_score_fit(
    session: AsyncSession, profile_id: uuid.UUID, research_plan_id: uuid.UUID | None
) -> dict[str, Any]:
    programs = await _researched_programs(session)
    created = 0
    for program in programs:
        reqs = (
            await session.execute(select(Requirement).where(Requirement.program_id == program.id))
        ).scalars().all()
        dims: list[DimensionInput] = [
            DimensionInput(
                dimension="academic",
                statuses=[r.status for r in reqs if r.requirement_type in ("academic", "cgpa")] or [
                    RequirementStatus.UNKNOWN
                ],
            ),
            DimensionInput(
                dimension="prerequisites",
                statuses=[r.status for r in reqs if r.requirement_type in ("prerequisite", "subjects")]
                or [RequirementStatus.UNKNOWN],
            ),
            DimensionInput(
                dimension="language",
                statuses=[r.status for r in reqs if r.requirement_type in ("language", "test")]
                or [RequirementStatus.UNKNOWN],
            ),
            DimensionInput(
                dimension="financial",
                statuses=[r.status for r in reqs if r.requirement_type in ("tuition", "fees", "budget")]
                or [RequirementStatus.UNKNOWN],
            ),
            # Dimensions without extracted requirements stay UNKNOWN (not zero):
            # missing evidence is uncertainty, not a failed criterion.
            DimensionInput("career", [RequirementStatus.UNKNOWN]),
            DimensionInput("timing", [RequirementStatus.UNKNOWN]),
            DimensionInput("evidence_confidence", [RequirementStatus.UNKNOWN]),
        ]
        score, subscores = overall_score(dims)
        breakdown = ", ".join(
            f"{k.replace('_', ' ').capitalize()} {v}" for k, v in subscores.items()
        )
        session.add(
            FitAssessment(
                research_plan_id=research_plan_id,
                profile_id=profile_id,
                program_id=program.id,
                overall_score=score,
                scoring_version=SCORING_VERSION,
                explanation=(
                    f"Weighted from your profile — {breakdown}. "
                    "This is a fit score, not an admission probability."
                ),
            )
        )
        created += 1
    await session.commit()
    return {"fit_assessments_created": created}


async def step_assess_risks(session: AsyncSession, profile_id: uuid.UUID) -> dict[str, Any]:
    from sqlalchemy import delete

    from app.db.models import StudentProfile

    reqs = (await session.execute(select(Requirement))).scalars().all()
    eligibility = [(r.normalized_key, r.status, r.mandatory) for r in reqs]
    program_ids = {r.program_id for r in reqs} | await _scope_program_ids(session, profile_id)
    now = datetime.now(UTC)

    # Real inputs (previously hardcoded): conflicts, stale evidence, deadline,
    # budget vs comparable tuition, document readiness.
    conflict_rows, conflict_evidence_ids = await _unresolved_conflicts(session, program_ids)
    stale_evidence_ids = await _stale_evidence_ids(session, program_ids, now)
    next_deadline = await _future_deadline(session, program_ids)
    deadline_evidence_ids = await _deadline_evidence_ids(session, program_ids, next_deadline)
    tuition_evidence_ids = await _claim_evidence_ids(session, program_ids, {"tuition_max", "budget_min"})
    key_evidence_ids = await _evidence_ids_by_key(session, program_ids)

    profile = await session.get(StudentProfile, profile_id)
    budget = profile.total_budget_amount if profile is not None else None
    estimated_cost = await _max_comparable_tuition(session, program_ids, profile)
    ready = await documents_ready(session, profile_id)

    ctx = RiskContext(
        eligibility=eligibility,
        budget=budget,
        estimated_cost=estimated_cost,
        next_deadline=next_deadline,
        documents_ready=ready,
        stale_evidence_count=len(stale_evidence_ids),
        conflicts=conflict_rows,
    )
    risks = assess(ctx)
    created = 0
    linked = 0
    new_titles = {risk.title for risk in risks}
    # Re-evaluation replaces prior OPEN risks; resolved/dismissed ones are kept.
    if new_titles:
        await session.execute(
            delete(Risk).where(
                Risk.profile_id == profile_id,
                Risk.status == RiskStatus.OPEN,
                Risk.title.in_(new_titles),
            )
        )
    for risk in risks:
        row = Risk(
            profile_id=profile_id,
            program_id=None,
            risk_type=risk.risk_type,
            severity=risk.severity,
            title=risk.title,
            reason=risk.reason,
            recommended_action=risk.recommended_action,
            status=RiskStatus.OPEN,
        )
        session.add(row)
        await session.flush()
        created += 1
        # RiskEvidence: link the exact evidence the risk derives from.
        for evidence_id in _evidence_for_risk(
            risk,
            key_evidence_ids=key_evidence_ids,
            conflict_evidence_ids=conflict_evidence_ids,
            stale_evidence_ids=stale_evidence_ids,
            deadline_evidence_ids=deadline_evidence_ids,
            tuition_evidence_ids=tuition_evidence_ids,
        ):
            session.add(RiskEvidence(risk_id=row.id, evidence_id=evidence_id))
            linked += 1
    await session.commit()
    return {
        "risks_created": created,
        "risk_evidence_links": linked,
        "unresolved_conflicts": len(conflict_rows),
        "stale_evidence_count": len(stale_evidence_ids),
        "documents_ready": ready,
    }


def _evidence_for_risk(
    risk: Any,
    *,
    key_evidence_ids: dict[str, list[uuid.UUID]],
    conflict_evidence_ids: list[uuid.UUID],
    stale_evidence_ids: list[uuid.UUID],
    deadline_evidence_ids: list[uuid.UUID],
    tuition_evidence_ids: list[uuid.UUID],
) -> list[uuid.UUID]:
    """Map a deterministic risk back to the evidence it derives from (bounded)."""
    ids: set[uuid.UUID] = set()
    title = risk.title
    if ": " in title and title.split(": ", 1)[0] in (
        "Requirement not met",
        "Requirement unverified",
        "Conflicting information for",
    ):
        key = title.split(": ", 1)[1]
        ids.update(key_evidence_ids.get(key, []))
    if risk.risk_type == "SOURCE_CONFLICT":
        ids.update(conflict_evidence_ids)
    if title == "Stale evidence":
        ids.update(stale_evidence_ids)
    if risk.risk_type == "DEADLINE":
        ids.update(deadline_evidence_ids)
    if risk.risk_type == "FINANCIAL" or title.startswith("Budget below"):
        ids.update(tuition_evidence_ids)
    return sorted(ids)[:10]


async def _unresolved_conflicts(
    session: AsyncSession, program_ids: set[uuid.UUID]
) -> tuple[list[str], list[uuid.UUID]]:
    """(human-readable descriptions, member evidence ids) for UNRESOLVED conflicts."""
    from app.db.models import EvidenceConflict

    if not program_ids:
        return [], []
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
    descriptions: list[str] = []
    evidence_ids: list[uuid.UUID] = []
    seen: set[str] = set()
    for conflict, evidence_id in rows:
        if conflict.conflict_key not in seen:
            seen.add(conflict.conflict_key)
            descriptions.append(conflict.description)
        evidence_ids.append(evidence_id)
    return descriptions, sorted(set(evidence_ids))


async def _stale_evidence_ids(
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


async def _claim_evidence_ids(
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


async def _evidence_ids_by_key(
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


async def _deadline_evidence_ids(
    session: AsyncSession, program_ids: set[uuid.UUID], deadline: date | None
) -> list[uuid.UUID]:
    if not program_ids or deadline is None:
        return []
    intake = (
        await session.execute(
            select(Intake).where(
                Intake.program_id.in_(program_ids),
                Intake.application_deadline == deadline,
            )
        )
    ).scalars().first()
    if intake is None or intake.evidence_id is None:
        return []
    return [intake.evidence_id]


async def _max_comparable_tuition(
    session: AsyncSession, program_ids: set[uuid.UUID], profile: Any
) -> Decimal | None:
    """Max in-currency portfolio tuition (see _financial_feasible formula)."""
    if not program_ids or profile is None or profile.budget_currency is None:
        return None
    rows = (
        await session.execute(
            select(Program.tuition_amount).where(
                Program.id.in_(program_ids),
                Program.tuition_amount.is_not(None),
                Program.tuition_currency == profile.budget_currency,
            )
        )
    ).scalars().all()
    amounts = [a for a in rows if a is not None]
    if not amounts:
        return None
    return max(amounts)


async def step_build_strategy(
    session: AsyncSession, profile_id: uuid.UUID, research_plan_id: uuid.UUID | None
) -> dict[str, Any]:
    fit_query = select(FitAssessment).where(FitAssessment.profile_id == profile_id)
    if research_plan_id is not None:
        # Only this run's assessments; earlier runs keep their own strategies.
        fit_query = fit_query.where(FitAssessment.research_plan_id == research_plan_id)
    fits = (
        await session.execute(fit_query.order_by(FitAssessment.overall_score.desc()))
    ).scalars().all()
    # A program may carry several fits (re-scored by a later run, or a
    # plan-less rebuild across runs): one program occupies exactly one
    # portfolio slot, and the query's score-desc order keeps the best fit.
    fits_by_program: dict[uuid.UUID, FitAssessment] = {}
    for fit in fits:
        fits_by_program.setdefault(fit.program_id, fit)
    fits = list(fits_by_program.values())
    candidates = [Candidate(program_id=str(f.program_id), fit_score=f.overall_score) for f in fits]
    portfolio = build_portfolio(candidates)
    portfolio_ids = {uuid.UUID(c.program_id) for items in portfolio.values() for c in items}
    if not portfolio_ids:
        portfolio_ids = {uuid.UUID(c.program_id) for c in candidates}

    # Real plan-health inputs (previously hardcoded to a perfect 100).
    health = await plan_health_inputs(session, profile_id, portfolio_ids)
    score = plan_health_score(
        blocker_count=health["blocker_count"],
        unresolved_conflicts=health["unresolved_conflicts"],
        stale_evidence_count=health["stale_evidence_count"],
        nearest_deadline=health["nearest_deadline"],
        financial_feasible=health["financial_feasible"],
        documents_ready=health["documents_ready"],
    )

    # Nearest future deadline per portfolio program, taken from extracted
    # intakes only — None when nothing was extracted (no invented dates).
    today = date.today()
    nearest_deadline: dict[uuid.UUID, date] = {}
    for _pid, _deadline in (
        await session.execute(
            select(Intake.program_id, Intake.application_deadline)
            .where(Intake.program_id.in_(portfolio_ids))
            .where(Intake.application_deadline.is_not(None))
            .where(Intake.application_deadline >= today)
        )
    ).all():
        if _deadline is not None and (_pid not in nearest_deadline or _deadline < nearest_deadline[_pid]):
            nearest_deadline[_pid] = _deadline

    run = StrategyRun(
        profile_id=profile_id,
        research_plan_id=research_plan_id,
        status=RunStatus.SUCCEEDED,
        scoring_version=SCORING_VERSION,
        strategy_version=STRATEGY_VERSION,
        summary=(
            f"{len(candidates)} programs scored · health: "
            f"{health['blocker_count']} blockers, "
            f"{health['unresolved_conflicts']} conflicts, "
            f"{health['stale_evidence_count']} stale claims"
        ),
        plan_health_score=score,
    )
    session.add(run)
    await session.flush()
    for category, items in portfolio.items():
        for i, cand in enumerate(items):
            session.add(
                ApplicationPlan(
                    strategy_run_id=run.id,
                    profile_id=profile_id,
                    program_id=uuid.UUID(cand.program_id),
                    category=category,
                    priority=i + 1,
                    rationale=f"Fit score {cand.fit_score}",
                    next_deadline=nearest_deadline.get(uuid.UUID(cand.program_id)),
                )
            )
    # Roadmap tasks derived only from rows that already exist (MASTER_SPEC §19
    # "Action roadmap"): extracted intake deadlines, unsatisfied mandatory
    # requirements, and conflicting claims on this portfolio. Dates come from
    # the intake records — nothing is invented; a program with no extracted
    # facts simply gets no task here.
    # (`today` was computed above for the intake window.)
    task_rows: list[RoadmapTask] = [
        RoadmapTask(
            strategy_run_id=run.id,
            profile_id=profile_id,
            title="Review top program requirements",
            task_type="VERIFY",
            status=TaskStatus.TODO,
        )
    ]
    seen_titles = {"Review top program requirements"}
    names = {
        p.id: p.canonical_name
        for p in (
            await session.execute(select(Program).where(Program.id.in_(portfolio_ids)))
        ).scalars()
    }

    def _add_task(task: RoadmapTask) -> None:
        if len(task_rows) >= 13 or task.title in seen_titles:
            return
        seen_titles.add(task.title)
        task_rows.append(task)

    intake_rows = (
        await session.execute(
            select(Intake)
            .where(Intake.program_id.in_(portfolio_ids))
            .where(Intake.application_deadline.is_not(None))
            .where(Intake.application_deadline >= today)
            .order_by(Intake.application_deadline.asc())
        )
    ).scalars().all()
    for intake in intake_rows:
        program_name = names.get(intake.program_id, "your program")
        deadline = intake.application_deadline
        if deadline is None:
            continue
        _add_task(
            RoadmapTask(
                strategy_run_id=run.id,
                profile_id=profile_id,
                program_id=intake.program_id,
                title=(
                    f"Apply to {program_name} — {intake.intake_label} deadline "
                    f"{deadline.isoformat()}"
                ),
                description="Deadline taken from the extracted intake record.",
                task_type="DEADLINE",
                due_date=deadline,
                priority=1,
                status=TaskStatus.TODO,
            )
        )
    requirement_rows = (
        await session.execute(
            select(Requirement)
            .where(Requirement.program_id.in_(portfolio_ids))
            .where(Requirement.mandatory.is_(True))
            .where(
                Requirement.status.in_(
                    (RequirementStatus.NOT_SATISFIED, RequirementStatus.CONFLICTING)
                )
            )
            .order_by(Requirement.normalized_key)
        )
    ).scalars().all()
    for req in requirement_rows:
        program_name = names.get(req.program_id, "your program")
        if req.status is RequirementStatus.CONFLICTING:
            _add_task(
                RoadmapTask(
                    strategy_run_id=run.id,
                    profile_id=profile_id,
                    program_id=req.program_id,
                    title=f"Verify conflicting information: {req.title} ({program_name})",
                    task_type="VERIFY",
                    status=TaskStatus.TODO,
                )
            )
        else:
            _add_task(
                RoadmapTask(
                    strategy_run_id=run.id,
                    profile_id=profile_id,
                    program_id=req.program_id,
                    title=f"Provide {req.title} for {program_name}",
                    task_type="REQUIREMENT",
                    status=TaskStatus.TODO,
                )
            )
    for task in task_rows:
        session.add(task)
    await session.commit()
    return {
        "strategy_run_id": str(run.id),
        "total_candidates": len(candidates),
        "roadmap_tasks_created": len(task_rows),
        "plan_health_score": str(score),
        "plan_health_inputs": {
            "blocker_count": health["blocker_count"],
            "unresolved_conflicts": health["unresolved_conflicts"],
            "stale_evidence_count": health["stale_evidence_count"],
            "nearest_deadline": (
                health["nearest_deadline"].isoformat() if health["nearest_deadline"] else None
            ),
            "financial_feasible": health["financial_feasible"],
            "documents_ready": health["documents_ready"],
        },
    }


async def _profile_facts(session: AsyncSession, profile_id: uuid.UUID) -> ProfileFacts:
    from app.db.models import StudentProfile, TestScore

    profile = await session.get(StudentProfile, profile_id)
    if profile is None:
        return ProfileFacts()
    # Built via the constructor so ProfileFacts.__post_init__ normalizes every
    # numeric fact to Decimal (ORM values may be float in-session).
    values: dict[str, Any] = {
        "cgpa": profile.cgpa,
        "cgpa_scale": profile.cgpa_scale,
        "percentage": profile.percentage,
        "backlogs": profile.backlogs or 0,
        "total_budget_amount": profile.total_budget_amount,
        "budget_currency": profile.budget_currency,
        "graduation_year": profile.graduation_year,
    }
    tests = (
        await session.execute(
            select(TestScore)
            .where(TestScore.profile_id == profile_id)
            .order_by(TestScore.test_date.desc())
            .limit(1)
        )
    ).scalars().first()
    if tests is not None:
        if tests.test_type.upper() in ("IELTS", "IELTS_ACADEMIC"):
            values["ielts_overall"] = tests.overall_score
        values["english_test_type"] = tests.test_type
    return ProfileFacts(**values)
