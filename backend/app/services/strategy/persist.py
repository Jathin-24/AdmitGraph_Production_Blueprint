"""Persistence helpers for strategy pipeline steps.

Inputs here are REAL data (requirements, evidence, conflicts, intakes,
documents, tuition, budget) — nothing in this module hard-codes a health score
or a risk. Formulas are documented next to their helpers. Every SQL statement
these steps run lives in ``app.db.repositories.strategy_pipeline`` (plus the
shared router repositories), and the pure scoring/risk derivations live in
``scoring`` / ``risk_assessment`` (re-exported here so existing imports keep
working); this module keeps the orchestration, business rules and the exact
commit points.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ApplicationPlan,
    EligibilityAssessment,
    EligibilityEvidence,
    Evidence,
    EvidenceStatus,
    FitAssessment,
    FitDimensionEvidence,
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
    StudentProfile,
    TaskStatus,
    TestScore,
)
from app.db.repositories import strategy_pipeline as pipeline
from app.db.repositories.strategies import get_program_evidence
from app.services.matching.evaluator import ProfileFacts, evaluate
from app.services.matching.normalize import normalize_percentage_to_cgpa
from app.services.risk.engine import EligibilityEntry, RiskContext, assess
from app.services.scoring.scorer import (
    DEFAULT_WEIGHTS,
    SCORING_VERSION,
    DimensionInput,
    overall_score,
)
from app.services.strategy.health import plan_health_score
from app.services.strategy.portfolio import Candidate, build_portfolio
from app.services.strategy.risk_assessment import (
    evidence_for_risk as _evidence_for_risk,
)
from app.services.strategy.risk_assessment import (
    risk_confidence as _risk_confidence,
)
from app.services.strategy.scoring import (
    eligibility_confidence as _eligibility_confidence,
)
from app.services.strategy.scoring import (
    estimated_cost as _estimated_cost,
)
from app.services.strategy.scoring import (
    evaluation_value as _evaluation_value,
)
from app.services.strategy.scoring import (
    evidence_dimension_statuses as evidence_dimension_statuses,
)
from app.services.strategy.scoring import (
    evidence_links_by_dimension as evidence_links_by_dimension,
)
from app.services.strategy.scoring import (
    facts_payload as _facts_payload,
)
from app.services.strategy.scoring import (
    top_reasons as top_reasons,
)

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

# The English tests onboarding can record. Any other TestScore row (GRE, GMAT,
# ...) is not an English result and must never stand in for one.
ENGLISH_TEST_TYPES = ("english_overall", "IELTS", "IELTS_ACADEMIC", "TOEFL", "PTE", "DET")


def _normalize_doc_type(raw: str) -> str:
    key = raw.strip().lower().replace(" ", "_").replace("-", "_")
    return _DOCUMENT_TYPE_ALIASES.get(key, key)


async def missing_core_documents(session: AsyncSession, profile_id: uuid.UUID) -> list[str]:
    """Core document types (MASTER_SPEC §17) that are not DONE yet.

    Returns them in checklist order; a type the student never tracked counts as
    missing (nothing tracked is not the same as ready).
    """
    rows = await pipeline.list_profile_documents(session, profile_id)
    by_type: dict[str, TaskStatus] = {}
    for row in rows:
        by_type[_normalize_doc_type(row.document_type)] = row.status
    return [t for t in CORE_DOCUMENT_TYPES if by_type.get(t) != TaskStatus.DONE]


async def documents_ready(session: AsyncSession, profile_id: uuid.UUID) -> bool:
    """True only when all core document types exist with status DONE."""
    return not await missing_core_documents(session, profile_id)


async def _scope_program_ids(session: AsyncSession, profile_id: uuid.UUID) -> set[uuid.UUID]:
    """Programs this profile is actually playing with: its scored fits plus the
    programs on its application plans.

    Requirements from unrelated programs (other students' research, fixture
    rows) must never raise risks against this profile.
    """
    fit_ids = await pipeline.fit_program_ids(session, profile_id)
    plan_ids = await pipeline.plan_program_ids(session, profile_id)
    return fit_ids | plan_ids


async def _future_deadline(
    session: AsyncSession, program_ids: set[uuid.UUID]
) -> date | None:
    """Earliest FUTURE application deadline across the given programs."""
    if not program_ids:
        return None
    deadlines = await pipeline.future_intake_deadlines(session, program_ids, date.today())
    return min(deadlines) if deadlines else None


async def _nearest_deadline_including_past(
    session: AsyncSession, program_ids: set[uuid.UUID]
) -> date | None:
    """Nearest deadline for the risk engine — past deadlines included.

    Earliest upcoming deadline when one exists; when EVERY known deadline has
    passed, the most recent one, so the engine raises its CRITICAL
    "Deadline has passed" risk instead of reporting "no deadline at all".
    """
    if not program_ids:
        return None
    deadlines = await pipeline.all_intake_deadlines(session, program_ids)
    if not deadlines:
        return None
    today = date.today()
    upcoming = [d for d in deadlines if d >= today]
    return min(upcoming) if upcoming else max(deadlines)


async def programs_with_passed_deadlines(
    session: AsyncSession, program_ids: set[uuid.UUID]
) -> set[uuid.UUID]:
    """Programs whose every extracted application deadline is in the past.

    TEST_PLAN "Deadline passed: program excluded or marked unavailable for the
    selected intake" — those programs leave the portfolio. A program with no
    extracted deadline is NOT returned: "no data" must never read as "missed".
    """
    if not program_ids:
        return set()
    rows = await pipeline.program_deadline_rows(session, program_ids)
    today = date.today()
    by_program: dict[uuid.UUID, list[date]] = {}
    for program_id, deadline in rows:
        by_program.setdefault(program_id, []).append(deadline)
    return {pid for pid, deadlines in by_program.items() if all(d < today for d in deadlines)}


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
            await pipeline.unsatisfied_mandatory_requirement_ids(session, program_ids)
        )
        evidence_rows = await get_program_evidence(session, program_ids)
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
    profile = await session.get(StudentProfile, profile_id)
    if profile is None:
        return True
    budget = profile.tuition_budget_amount or profile.total_budget_amount
    if budget is None or not program_ids:
        return True
    tuition_rows = await pipeline.portfolio_tuition_amounts(
        session, program_ids, profile.budget_currency
    )
    if not tuition_rows:
        return True  # UNKNOWN: no comparable tuition figure
    amounts = [a for a in tuition_rows if a is not None]
    return all(amount <= budget for amount in amounts)


async def _conflicting_claim_keys(
    session: AsyncSession, program_ids: set[uuid.UUID]
) -> dict[uuid.UUID, set[str]]:
    """Program -> normalized keys whose evidence is CONFLICTING."""
    return await pipeline.conflicting_claim_keys(session, program_ids)


async def _evidence_keys_by_program(
    session: AsyncSession, program_ids: set[uuid.UUID]
) -> dict[tuple[uuid.UUID, str], list[uuid.UUID]]:
    """(program, claim key) -> evidence ids, for eligibility evidence links."""
    return await pipeline.evidence_keys_by_program(session, program_ids)


async def _latest_fits_by_program(
    session: AsyncSession, profile_id: uuid.UUID
) -> dict[uuid.UUID, FitAssessment]:
    """Newest fit per program for this profile (created_at is server-set, so a
    tie falls back to insertion order)."""
    return await pipeline.latest_fits_by_program(session, profile_id)


async def step_evaluate_requirements(session: AsyncSession, profile_id: uuid.UUID) -> dict[str, Any]:
    programs = await pipeline.researched_programs(session)
    program_ids = {p.id for p in programs}
    conflicting_keys = await _conflicting_claim_keys(session, program_ids)
    evidence_keys = await _evidence_keys_by_program(session, program_ids)
    facts = await _profile_facts(session, profile_id)
    facts_payload = _facts_payload(facts)
    fits = await _latest_fits_by_program(session, profile_id)

    fit_ids = [f.id for f in fits.values()]
    existing: dict[tuple[uuid.UUID, uuid.UUID], EligibilityAssessment] = {}
    if fit_ids:
        rows = await pipeline.eligibility_assessments_by_fit_ids(session, fit_ids)
        existing = {(row.fit_assessment_id, row.requirement_id): row for row in rows}
    linked: set[tuple[uuid.UUID, uuid.UUID]] = set()
    if existing:
        link_rows = await pipeline.eligibility_evidence_links(
            session, [row.id for row in existing.values()]
        )
        linked = {(row.eligibility_assessment_id, row.evidence_id) for row in link_rows}

    evaluated = 0
    created = 0
    updated = 0
    skipped = 0
    evidence_links = 0
    for program in programs:
        reqs = await pipeline.program_requirements(session, program.id)
        fit = fits.get(program.id)
        for req in reqs:
            status, reason = evaluate(
                {
                    "normalized_key": req.normalized_key,
                    "operator": req.operator or "",
                    "value": _evaluation_value(req),
                },
                facts,
            )
            # Conflicting evidence on this program+key wins: never silently
            # choose a value (MASTER_SPEC §11). Surfaced as CONFLICTING in the
            # requirements API and as a SOURCE_CONFLICT risk.
            if req.normalized_key in conflicting_keys.get(program.id, set()):
                status = RequirementStatus.CONFLICTING
                reason = "Stored sources disagree about this claim; the conflict stands until resolved."
            req.status = status
            evaluated += 1
            if fit is None:
                # Eligibility rows hang off a fit, and this run mints its fits
                # in the NEXT step (score_fit): a first run writes none and
                # says how many it skipped rather than inventing a link.
                skipped += 1
                continue
            evidence_ids = evidence_keys.get((program.id, req.normalized_key), [])
            confidence = _eligibility_confidence(status, bool(evidence_ids))
            row = existing.get((fit.id, req.id))
            if row is None:
                row = EligibilityAssessment(
                    fit_assessment_id=fit.id,
                    requirement_id=req.id,
                    status=status,
                    matched_value=facts_payload,
                    expected_value=dict(req.value or {}),
                    reason=reason,
                    confidence=confidence,
                )
                session.add(row)
                await session.flush()
                created += 1
            else:
                row.status = status
                row.matched_value = facts_payload
                row.expected_value = dict(req.value or {})
                row.reason = reason
                row.confidence = confidence
                updated += 1
            for evidence_id in evidence_ids:
                if (row.id, evidence_id) in linked:
                    continue
                session.add(
                    EligibilityEvidence(
                        eligibility_assessment_id=row.id, evidence_id=evidence_id
                    )
                )
                linked.add((row.id, evidence_id))
                evidence_links += 1
    await session.commit()
    return {
        "requirements_evaluated": evaluated,
        "eligibility_assessments_written": created,
        "eligibility_assessments_updated": updated,
        "eligibility_assessments_skipped": skipped,
        "eligibility_evidence_links": evidence_links,
    }


async def step_score_fit(
    session: AsyncSession, profile_id: uuid.UUID, research_plan_id: uuid.UUID | None
) -> dict[str, Any]:
    programs = await pipeline.researched_programs(session)
    program_ids = {p.id for p in programs}
    evidence_by_program: dict[uuid.UUID, list[Evidence]] = {}
    if program_ids:
        rows = await get_program_evidence(session, program_ids)
        for row in rows:
            if row.subject_id is not None:
                evidence_by_program.setdefault(row.subject_id, []).append(row)

    created = 0
    linked = 0
    for program in programs:
        reqs = await pipeline.program_requirements(session, program.id)
        evidence_rows = evidence_by_program.get(program.id, [])
        derived = evidence_dimension_statuses(evidence_rows)
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
                statuses=[r.status for r in reqs if r.requirement_type in ("language", "test")] or [
                    RequirementStatus.UNKNOWN
                ],
            ),
            DimensionInput(
                dimension="financial",
                statuses=[r.status for r in reqs if r.requirement_type in ("tuition", "fees", "budget")]
                or [RequirementStatus.UNKNOWN],
            ),
            # Dimensions without extracted requirements stay UNKNOWN (not zero):
            # missing evidence is uncertainty, not a failed criterion. Career,
            # timing and evidence confidence are derived from stored evidence.
            DimensionInput("career", [derived["career"]]),
            DimensionInput("timing", [derived["timing"]]),
            DimensionInput("evidence_confidence", [derived["evidence_confidence"]]),
        ]
        dimension_statuses = {d.dimension: list(d.statuses) for d in dims}
        score, subscores = overall_score(dims)
        breakdown = ", ".join(
            f"{k.replace('_', ' ').capitalize()} {v}" for k, v in subscores.items()
        )
        fit = FitAssessment(
            research_plan_id=research_plan_id,
            profile_id=profile_id,
            program_id=program.id,
            academic_score=subscores.get("academic"),
            prerequisite_score=subscores.get("prerequisites"),
            language_score=subscores.get("language"),
            financial_score=subscores.get("financial"),
            career_score=subscores.get("career"),
            timing_score=subscores.get("timing"),
            evidence_confidence_score=subscores.get("evidence_confidence"),
            overall_score=score,
            scoring_version=SCORING_VERSION,
            explanation=(
                f"Weighted from your profile — {breakdown}. "
                "This is a fit score, not an admission probability."
            ),
            # Recalculation snapshot: the exact inputs this score came from.
            profile_snapshot={
                "scoring_version": SCORING_VERSION,
                "weights": {name: str(weight) for name, weight in DEFAULT_WEIGHTS.items()},
                "requirements": [
                    {
                        "id": str(r.id),
                        "key": r.normalized_key,
                        "type": r.requirement_type,
                        "mandatory": bool(r.mandatory),
                        "status": r.status.value,
                        "value": dict(r.value or {}),
                    }
                    for r in reqs
                ],
                "dimensions": {
                    name: [s.value for s in statuses] for name, statuses in dimension_statuses.items()
                },
            },
        )
        session.add(fit)
        await session.flush()
        created += 1
        for dimension, evidence_ids in evidence_links_by_dimension(evidence_rows).items():
            for evidence_id in evidence_ids:
                session.add(
                    FitDimensionEvidence(
                        fit_assessment_id=fit.id, dimension=dimension, evidence_id=evidence_id
                    )
                )
                linked += 1
    await session.commit()
    return {"fit_assessments_created": created, "fit_dimension_evidence_links": linked}


async def _latest_english_test(session: AsyncSession, profile_id: uuid.UUID) -> TestScore | None:
    """Most recent English test on record (onboarding's own type vocabulary).

    Test rows of any other kind (GRE, GMAT, ...) never stand in for an English
    result, and a score without an expiry date keeps expiry UNKNOWN.
    """
    return await pipeline.latest_english_test(session, profile_id, ENGLISH_TEST_TYPES)


async def _education_facts(session: AsyncSession, profile_id: uuid.UUID) -> tuple[set[str], Decimal | None]:
    """(subject labels, summed credits) recorded on the profile's education.

    The credit sum is the only credit history the profile carries — None when
    no subject records credits (never a guessed total).
    """
    rows = await pipeline.education_subject_rows(session, profile_id)
    subjects: set[str] = set()
    total = Decimal(0)
    saw_credits = False
    for name, normalized, credits in rows:
        label = str(normalized or name or "").strip().lower()
        if label:
            subjects.add(label)
        if credits is not None:
            total += credits
            saw_credits = True
    return subjects, (total if saw_credits else None)


async def _profile_facts(session: AsyncSession, profile_id: uuid.UUID) -> ProfileFacts:
    profile = await session.get(StudentProfile, profile_id)
    if profile is None:
        return ProfileFacts()
    # Built via the constructor so ProfileFacts.__post_init__ normalizes every
    # numeric fact to Decimal (ORM values may be float in-session).
    cgpa = profile.cgpa
    if cgpa is None and profile.percentage is not None and profile.cgpa_scale is not None:
        # A percentage only means something on a KNOWN scale — derive the
        # comparable CGPA there and nowhere else.
        cgpa = normalize_percentage_to_cgpa(profile.percentage, profile.cgpa_scale)
    subjects, matched_credits = await _education_facts(session, profile_id)
    values: dict[str, Any] = {
        "cgpa": cgpa,
        "cgpa_scale": profile.cgpa_scale,
        "percentage": profile.percentage,
        "backlogs": profile.backlogs or 0,
        "total_budget_amount": profile.total_budget_amount,
        "budget_currency": profile.budget_currency,
        "graduation_year": profile.graduation_year,
        "subjects": subjects,
        "matched_credits": matched_credits,
    }
    test = await _latest_english_test(session, profile_id)
    if test is not None:
        values["english_test_type"] = test.test_type
        values["language_score"] = test.overall_score
        values["language_expiry_date"] = test.expiry_date
        if test.test_type.strip().upper() in ("IELTS", "IELTS_ACADEMIC"):
            values["ielts_overall"] = test.overall_score
    return ProfileFacts(**values)


async def _unresolved_conflicts(
    session: AsyncSession, program_ids: set[uuid.UUID]
) -> tuple[list[str], list[uuid.UUID]]:
    """(human-readable descriptions, member evidence ids) for UNRESOLVED conflicts."""
    if not program_ids:
        return [], []
    rows = await pipeline.unresolved_conflict_rows(session, program_ids)
    descriptions: list[str] = []
    evidence_ids: list[uuid.UUID] = []
    seen: set[str] = set()
    for conflict, evidence_id in rows:
        if conflict.conflict_key not in seen:
            seen.add(conflict.conflict_key)
            descriptions.append(conflict.description)
        evidence_ids.append(evidence_id)
    return descriptions, sorted(set(evidence_ids))


async def _deadline_evidence_ids(
    session: AsyncSession, program_ids: set[uuid.UUID], deadline: date | None
) -> list[uuid.UUID]:
    intake = await pipeline.intake_at_deadline(session, program_ids, deadline)
    if intake is None or intake.evidence_id is None:
        return []
    return [intake.evidence_id]


async def _max_comparable_tuition(
    session: AsyncSession, program_ids: set[uuid.UUID], profile: Any
) -> Decimal | None:
    """Max in-currency portfolio tuition (see _financial_feasible formula)."""
    if not program_ids or profile is None or profile.budget_currency is None:
        return None
    rows = await pipeline.portfolio_tuition_amounts(
        session, program_ids, profile.budget_currency
    )
    amounts = [a for a in rows if a is not None]
    if not amounts:
        return None
    return max(amounts)


async def portfolio_risk_stats(
    session: AsyncSession, profile_id: uuid.UUID
) -> dict[uuid.UUID, tuple[int, str]]:
    """OPEN risks grouped by program: (count, worst severity) — see repository."""
    return await pipeline.portfolio_risk_stats(session, profile_id)


async def step_assess_risks(session: AsyncSession, profile_id: uuid.UUID) -> dict[str, Any]:
    # Scoped to this profile's own programs: only its fits and its plans.
    program_ids = await _scope_program_ids(session, profile_id)
    reqs = (
        list(await pipeline.requirements_for_programs(session, program_ids))
        if program_ids
        else []
    )
    # 4-element entries link each eligibility risk back to its Requirement row.
    eligibility: list[EligibilityEntry] = [
        (r.normalized_key, r.status, r.mandatory, r.id) for r in reqs
    ]
    req_program = {r.id: r.program_id for r in reqs}
    now = datetime.now(UTC)

    # Real inputs: conflicts, stale evidence, nearest deadline (past included),
    # budget vs comparable tuition, document readiness, career/language gaps.
    conflict_rows, conflict_evidence_ids = await _unresolved_conflicts(session, program_ids)
    stale_evidence_ids = await pipeline.stale_evidence_ids(session, program_ids, now)
    next_deadline = await _nearest_deadline_including_past(session, program_ids)
    deadline_evidence_ids = await _deadline_evidence_ids(session, program_ids, next_deadline)
    tuition_evidence_ids = await pipeline.claim_evidence_ids(
        session, program_ids, {"tuition_max", "budget_min"}
    )
    key_evidence_ids = await pipeline.evidence_ids_by_key(session, program_ids)
    career_missing = await pipeline.programs_without_career_evidence(session, program_ids)
    missing_docs = await missing_core_documents(session, profile_id)

    profile = await session.get(StudentProfile, profile_id)
    test = await _latest_english_test(session, profile_id)
    # Tri-state: None = nothing known about the student's tests (silent),
    # True/False = a test is (not) on record for a profile we have.
    language_present = None if profile is None else test is not None
    language_expiry = test.expiry_date if test is not None else None
    language_test_evidence_ids = (
        [test.evidence_id] if test is not None and test.evidence_id is not None else []
    )

    budget = profile.total_budget_amount if profile is not None else None
    estimated_cost = await _max_comparable_tuition(session, program_ids, profile)
    ready = not missing_docs

    ctx = RiskContext(
        eligibility=eligibility,
        budget=budget,
        estimated_cost=estimated_cost,
        next_deadline=next_deadline,
        documents_ready=ready,
        missing_documents=missing_docs,
        stale_evidence_count=len(stale_evidence_ids),
        conflicts=conflict_rows,
        programs_without_career_evidence=career_missing,
        language_test_present=language_present,
        language_test_expiry=language_expiry,
    )
    risks = assess(ctx)

    # Full replacement of the OPEN set: this engine is the only producer of
    # Risk rows, so re-running it re-derives the whole OPEN portfolio instead
    # of appending duplicates. Resolved/dismissed/acknowledged rows are the
    # student's history and are kept.
    open_ids = await pipeline.open_risk_ids(session, profile_id)
    risks_deleted = len(open_ids)
    if open_ids:
        await pipeline.delete_risks(session, open_ids)

    created = 0
    linked = 0
    for risk in risks:
        row = Risk(
            profile_id=profile_id,
            program_id=(
                risk.program_id
                if risk.program_id is not None
                else (req_program.get(risk.requirement_id) if risk.requirement_id is not None else None)
            ),
            requirement_id=risk.requirement_id,
            risk_type=risk.risk_type,
            severity=risk.severity,
            title=risk.title,
            reason=risk.reason,
            recommended_action=risk.recommended_action,
            status=RiskStatus.OPEN,
            confidence=_risk_confidence(risk),
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
            language_test_evidence_ids=language_test_evidence_ids,
        ):
            session.add(RiskEvidence(risk_id=row.id, evidence_id=evidence_id))
            linked += 1
    await session.commit()
    return {
        "risks_created": created,
        "risks_deleted": risks_deleted,
        "risk_evidence_links": linked,
        "unresolved_conflicts": len(conflict_rows),
        "stale_evidence_count": len(stale_evidence_ids),
        "documents_ready": ready,
        "missing_documents": missing_docs,
        "programs_without_career_evidence": len(career_missing),
    }


async def step_build_strategy(
    session: AsyncSession, profile_id: uuid.UUID, research_plan_id: uuid.UUID | None
) -> dict[str, Any]:
    fits = await pipeline.strategy_fits(session, profile_id, research_plan_id)
    # A program may carry several fits (re-scored by a later run, or a
    # plan-less rebuild across runs): one program occupies exactly one
    # portfolio slot, and the query's score-desc order keeps the best fit.
    fits_by_program: dict[uuid.UUID, FitAssessment] = {}
    for fit in fits:
        fits_by_program.setdefault(fit.program_id, fit)
    fits = list(fits_by_program.values())
    scored_ids = {f.program_id for f in fits}

    programs: dict[uuid.UUID, Program] = {}
    if scored_ids:
        programs = await pipeline.programs_by_ids(session, scored_ids)

    # TEST_PLAN "Deadline passed -> program excluded": a program whose every
    # known deadline has passed leaves the portfolio, and the ids are reported
    # (never silently dropped). No extracted deadline = no exclusion.
    excluded_ids = await programs_with_passed_deadlines(session, scored_ids)
    risk_stats = await portfolio_risk_stats(session, profile_id)

    candidates: list[Candidate] = []
    excluded: list[str] = []
    for fit in fits:
        pid = fit.program_id
        if pid in excluded_ids:
            excluded.append(str(pid))
            continue
        program = programs.get(pid)
        stats = risk_stats.get(pid)
        candidates.append(
            Candidate(
                program_id=str(pid),
                fit_score=fit.overall_score,
                risk_count=stats[0] if stats else 0,
                top_risk_severity=stats[1] if stats else None,
                institution_id=str(program.institution_id) if program is not None else None,
                country_code=program.country_code if program is not None else None,
            )
        )
    portfolio_result = build_portfolio(candidates)
    tiers = portfolio_result.tiers
    portfolio_ids = {uuid.UUID(c.program_id) for items in tiers.values() for c in items}
    if not portfolio_ids:
        # Nothing selected: health and roadmap still ground on this run's own
        # scored programs instead of an empty set.
        portfolio_ids = scored_ids

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

    # Nearest FUTURE deadline per portfolio program, taken from extracted
    # intakes only — None when nothing was extracted (no invented dates).
    today = date.today()
    nearest_deadline: dict[uuid.UUID, date] = {}
    if portfolio_ids:
        for _pid, _deadline in await pipeline.nearest_future_intake_rows(
            session, portfolio_ids, today
        ):
            if _pid not in nearest_deadline or _deadline < nearest_deadline[_pid]:
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
    for category, items in tiers.items():
        for i, cand in enumerate(items):
            pid = uuid.UUID(cand.program_id)
            plan_fit = fits_by_program.get(pid)
            deadline = nearest_deadline.get(pid)
            session.add(
                ApplicationPlan(
                    strategy_run_id=run.id,
                    profile_id=profile_id,
                    program_id=pid,
                    category=category,
                    priority=i + 1,
                    fit_assessment_id=plan_fit.id if plan_fit is not None else None,
                    rationale=f"Fit score {cand.fit_score}",
                    reasons=top_reasons(plan_fit),
                    estimated_cost=_estimated_cost(programs.get(pid)),
                    next_deadline=deadline,
                    next_action=(
                        f"Apply by {deadline.isoformat()}" if deadline is not None else None
                    ),
                )
            )
    # Roadmap tasks derived only from rows that already exist (MASTER_SPEC §19
    # "Action roadmap"): extracted intake deadlines, unsatisfied mandatory
    # requirements, and conflicting claims on this portfolio. Dates come from
    # the intake records — nothing is invented; a program with no extracted
    # facts simply gets no task here.
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
    names: dict[uuid.UUID, str] = {}
    if portfolio_ids:
        names = {
            p.id: p.canonical_name
            for p in (await pipeline.programs_by_ids(session, portfolio_ids)).values()
        }

    # Evidence links for derived tasks (evidence-metadata rule): every task
    # points back to the rows that justify it — the claim evidence behind a
    # requirement (including conflicting sources) and the deadline evidence
    # behind an intake. Generic tasks stay unlinked rather than guessing.
    evidence_by_program_key: dict[tuple[uuid.UUID, str], list[uuid.UUID]] = {}
    if portfolio_ids:
        evidence_by_program_key = await pipeline.all_evidence_keys_by_program(
            session, portfolio_ids
        )

    def _task_evidence(program_id: uuid.UUID, claim_key: str) -> list[str]:
        """Grounded evidence ids (JSON-string form) for one task, bounded."""
        return [str(e) for e in sorted(evidence_by_program_key.get((program_id, claim_key), []))[:10]]

    def _add_task(task: RoadmapTask) -> None:
        if len(task_rows) >= 13 or task.title in seen_titles:
            return
        seen_titles.add(task.title)
        task_rows.append(task)

    intake_rows: list[Intake] = []
    if portfolio_ids:
        intake_rows = await pipeline.future_intakes(session, portfolio_ids, today)
    for intake in intake_rows:
        program_name = names.get(intake.program_id, "your program")
        deadline = intake.application_deadline
        if deadline is None:
            continue
        linked = set(_task_evidence(intake.program_id, "application_deadline"))
        if intake.evidence_id is not None:
            linked.add(str(intake.evidence_id))  # the exact source this date came from
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
                evidence_ids=sorted(linked)[:10],
            )
        )
    requirement_rows: list[Requirement] = []
    if portfolio_ids:
        requirement_rows = await pipeline.mandatory_problem_requirements(session, portfolio_ids)
    for req in requirement_rows:
        program_name = names.get(req.program_id, "your program")
        req_evidence = _task_evidence(req.program_id, req.normalized_key)
        if req.status is RequirementStatus.CONFLICTING:
            _add_task(
                RoadmapTask(
                    strategy_run_id=run.id,
                    profile_id=profile_id,
                    program_id=req.program_id,
                    title=f"Verify conflicting information: {req.title} ({program_name})",
                    task_type="VERIFY",
                    status=TaskStatus.TODO,
                    evidence_ids=req_evidence,
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
                    evidence_ids=req_evidence,
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
        "broadened": portfolio_result.broadened,
        "broadened_reason": portfolio_result.broadened_reason,
        "passed_deadline_excluded": excluded,
        "passed_deadline_excluded_count": len(excluded),
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
