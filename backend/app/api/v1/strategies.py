import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.models import (
    CounterfactualRun,
    EvidenceStatus,
    FitAssessment,
    Risk,
    RiskStatus,
    StrategyRun,
    StudentProfile,
)
from app.db.repositories import strategies as strategies_repo
from app.db.session import get_session
from app.services.profile import get_or_create_profile
from app.services.strategy.persist import top_reasons
from app.services.strategy.simulator import (
    SCENARIOS,
    apply_scenario,
    compute_delta,
    recompute_portfolio,
)

router = APIRouter(tags=["strategies"])

_SEVERITY_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}

# The three risk states a student may move a risk into (PATCH below). OPEN is
# the engine's state, never a target: risks are re-derived on every run.
_RISK_TRANSITIONS = ("ACKNOWLEDGED", "RESOLVED", "DISMISSED")


async def _get_strategy(session: AsyncSession, strategy_id: uuid.UUID) -> StrategyRun:
    """Load a strategy that belongs to the caller's profile, else 404.

    Same rule as documents: another user's strategy id is indistinguishable
    from a non-existent one, so cross-user probing stays blind.
    """
    profile = await get_or_create_profile(session)
    strategy = await strategies_repo.get_strategy_run(session, strategy_id)
    if strategy is None or strategy.profile_id != profile.id:
        raise AppError(404, "NOT_FOUND", "Strategy not found")
    return strategy


async def _portfolio_rows(
    session: AsyncSession, strategy: StrategyRun
) -> list[dict[str, Any]]:
    """Application-plan cards with the FRONTEND_SPEC strategy-card fields.

    Everything quoted here is a stored value: fit score, the plan's top-2
    dimension reasons, its cost band, the worst OPEN risk for that program and
    the evidence freshness of its claims. Missing data stays missing (None /
    UNKNOWN) rather than being invented.
    """
    plans = await strategies_repo.get_portfolio_plans(session, strategy.id)
    if not plans:
        return []
    program_ids = {p.program_id for p in plans}

    # Fit for a card: the one the plan points at, else this profile's latest
    # fit for that program (legacy plans were written before the link existed).
    fits = await strategies_repo.get_portfolio_fits(session, strategy.profile_id, program_ids)
    fit_by_id = {f.id: f for f in fits}
    fit_by_program: dict[uuid.UUID, FitAssessment] = {}
    for row_fit in fits:
        fit_by_program.setdefault(row_fit.program_id, row_fit)

    # Worst OPEN risk per program (portfolio-scoped: a program with no
    # program-level risk shows null, not the portfolio's budget worry).
    risk_by_program: dict[uuid.UUID, Risk] = {}
    open_risks = await strategies_repo.get_open_program_risks(
        session, strategy.profile_id, program_ids
    )
    for row_risk in open_risks:
        if row_risk.program_id is None:
            continue
        current = risk_by_program.get(row_risk.program_id)
        if current is None or _SEVERITY_RANK.get(
            row_risk.severity.value, 9
        ) < _SEVERITY_RANK.get(current.severity.value, 9):
            risk_by_program[row_risk.program_id] = row_risk

    # Evidence freshness per program: UNKNOWN with no claims, STALE when any
    # claim is marked stale or past its freshness deadline, else FRESH.
    now = datetime.now(UTC)
    evidence_by_program: dict[uuid.UUID, dict[str, Any]] = {}
    evidence_rows = await strategies_repo.get_program_evidence(session, program_ids)
    for row in evidence_rows:
        if row.subject_id is None:
            continue
        counts = evidence_by_program.setdefault(row.subject_id, {"total": 0, "stale": 0})
        counts["total"] += 1
        if row.status is EvidenceStatus.STALE or (
            row.freshness_deadline is not None and row.freshness_deadline < now
        ):
            counts["stale"] += 1

    rows: list[dict[str, Any]] = []
    for plan in plans:
        linked_fit = (
            fit_by_id.get(plan.fit_assessment_id)
            if plan.fit_assessment_id is not None
            else None
        )
        fit = linked_fit or fit_by_program.get(plan.program_id)
        risk = risk_by_program.get(plan.program_id)
        freshness = evidence_by_program.get(plan.program_id)
        if freshness is None:
            freshness_status, stale, total = "UNKNOWN", 0, 0
        elif freshness["stale"]:
            freshness_status, stale, total = (
                "STALE",
                int(freshness["stale"]),
                int(freshness["total"]),
            )
        else:
            freshness_status, stale, total = "FRESH", 0, int(freshness["total"])
        rows.append(
            {
                "program_id": str(plan.program_id),
                "program_name": plan.program.canonical_name if plan.program else None,
                "institution": (
                    plan.program.institution.canonical_name
                    if plan.program and plan.program.institution
                    else None
                ),
                "category": plan.category.value,
                "priority": plan.priority,
                "rationale": plan.rationale,
                "fit_score": str(fit.overall_score) if fit is not None else None,
                "reasons": list(plan.reasons) if plan.reasons else top_reasons(fit),
                "top_risk": (
                    {
                        "id": str(risk.id),
                        "severity": risk.severity.value,
                        "risk_type": risk.risk_type,
                        "title": risk.title,
                    }
                    if risk is not None
                    else None
                ),
                "estimated_cost": dict(plan.estimated_cost or {}),
                "next_deadline": (
                    plan.next_deadline.isoformat() if plan.next_deadline else None
                ),
                "next_action": plan.next_action,
                "evidence_freshness": {
                    "status": freshness_status,
                    "stale": stale,
                    "total": total,
                },
            }
        )
    return rows


async def _risk_rows(session: AsyncSession, profile_id: uuid.UUID) -> list[dict[str, Any]]:
    risks = sorted(
        await strategies_repo.get_profile_risks(session, profile_id),
        key=lambda r: (_SEVERITY_RANK.get(r.severity.value, 9), r.risk_type),
    )
    return [
        {
            "id": str(r.id),
            "risk_type": r.risk_type,
            "severity": r.severity.value,
            "title": r.title,
            "reason": r.reason,
            "recommended_action": r.recommended_action,
            "status": r.status.value,
            "confidence": r.confidence.value,
            "program_id": str(r.program_id) if r.program_id else None,
            "requirement_id": str(r.requirement_id) if r.requirement_id else None,
            "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
        }
        for r in risks
    ]


async def _task_rows(session: AsyncSession, strategy_id: uuid.UUID) -> list[dict[str, Any]]:
    tasks = await strategies_repo.get_roadmap_tasks(session, strategy_id)
    return [
        {
            "id": str(t.id),
            "title": t.title,
            "task_type": t.task_type,
            "status": t.status.value,
            "due_date": t.due_date.isoformat() if t.due_date else None,
            "evidence_ids": [str(e) for e in (t.evidence_ids or [])],
        }
        for t in tasks
    ]


@router.get("/strategies")
async def list_strategies(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    profile = await get_or_create_profile(session)
    strategies = await strategies_repo.list_strategy_runs(session, profile.id)
    return {
        "items": [
            {
                "id": str(s.id),
                "status": s.status.value,
                "plan_health_score": str(s.plan_health_score) if s.plan_health_score is not None else None,
                "summary": s.summary,
                "created_at": s.created_at.isoformat(),
            }
            for s in strategies
        ]
    }


@router.get("/strategies/{strategy_id}")
async def get_strategy(
    strategy_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    strategy = await _get_strategy(session, strategy_id)
    return {
        "id": str(strategy.id),
        "status": strategy.status.value,
        "plan_health_score": (
            str(strategy.plan_health_score) if strategy.plan_health_score is not None else None
        ),
        "summary": strategy.summary,
        "scoring_version": strategy.scoring_version,
        "strategy_version": strategy.strategy_version,
        "created_at": strategy.created_at.isoformat(),
        "portfolio": await _portfolio_rows(session, strategy),
        "risks": await _risk_rows(session, strategy.profile_id),
        "roadmap_tasks": await _task_rows(session, strategy_id),
    }


@router.get("/strategies/{strategy_id}/portfolio")
async def get_portfolio(
    strategy_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    strategy = await _get_strategy(session, strategy_id)
    return {"items": await _portfolio_rows(session, strategy)}


@router.get("/strategies/{strategy_id}/risks")
async def get_risks(
    strategy_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    strategy = await _get_strategy(session, strategy_id)
    return {"items": await _risk_rows(session, strategy.profile_id)}


@router.patch("/strategies/{strategy_id}/risks/{risk_id}")
async def update_risk(
    strategy_id: uuid.UUID,
    risk_id: uuid.UUID,
    payload: dict[str, Any],
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Move one of the caller's risks to ACKNOWLEDGED / RESOLVED / DISMISSED.

    The risk must belong to the strategy's own profile (otherwise 404, same
    probing rule as the rest of the router). RESOLVED stamps `resolved_at`;
    every other transition clears it, so an un-resolved risk never keeps a
    stale resolution date. OPEN is not a target: risks are re-derived by the
    risk engine on the next run.
    """
    strategy = await _get_strategy(session, strategy_id)
    # Strict, case-sensitive contract: the three exact transition values only.
    status = str(payload.get("status", "")).strip()
    if status not in _RISK_TRANSITIONS:
        raise AppError(
            422,
            "VALIDATION_ERROR",
            f"Invalid risk status. Allowed: {list(_RISK_TRANSITIONS)}",
        )
    risk = await strategies_repo.get_risk(session, risk_id)
    if risk is None or risk.profile_id != strategy.profile_id:
        raise AppError(404, "NOT_FOUND", "Risk not found")
    risk.status = RiskStatus(status)
    risk.resolved_at = datetime.now(UTC) if status == "RESOLVED" else None
    await strategies_repo.commit(session)
    rows = await _risk_rows(session, strategy.profile_id)
    updated = next((row for row in rows if row["id"] == str(risk.id)), None)
    return {"risk": updated}


@router.get("/strategies/{strategy_id}/roadmap")
async def get_roadmap(
    strategy_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    await _get_strategy(session, strategy_id)
    return {"items": await _task_rows(session, strategy_id)}


@router.get("/strategies/{strategy_id}/evidence-health")
async def get_evidence_health(
    strategy_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """Evidence coverage across the programs in this strategy's portfolio."""
    await _get_strategy(session, strategy_id)
    program_ids = await strategies_repo.get_portfolio_program_ids(session, strategy_id)
    if not program_ids:
        return {
            "programs_total": 0,
            "programs_with_evidence": 0,
            "evidence_total": 0,
            "by_status": {},
            "by_confidence": {},
            "by_authority": {},
            "stale_count": 0,
            "unknowns": 0,
            "conflicting_count": 0,
        }
    rows = await strategies_repo.get_program_evidence(session, program_ids)
    by_status: dict[str, int] = {}
    by_confidence: dict[str, int] = {}
    by_authority: dict[str, int] = {}
    covered: set[uuid.UUID] = set()
    now = datetime.now(UTC)
    stale = 0
    unknowns = 0
    source_ids = {e.source_id for e in rows}
    authorities: dict[uuid.UUID, str] = {}
    if source_ids:
        source_rows = await strategies_repo.get_source_authorities(session, source_ids)
        for source_id, authority in source_rows:
            authorities[source_id] = authority.value
    for e in rows:
        by_status[e.status.value] = by_status.get(e.status.value, 0) + 1
        by_confidence[e.confidence.value] = by_confidence.get(e.confidence.value, 0) + 1
        authority_key = authorities.get(e.source_id, "UNKNOWN")
        by_authority[authority_key] = by_authority.get(authority_key, 0) + 1
        if e.subject_id:
            covered.add(e.subject_id)
        if e.freshness_deadline is not None and e.freshness_deadline < now:
            stale += 1
        if e.status is EvidenceStatus.UNAVAILABLE:
            unknowns += 1
    return {
        "programs_total": len(program_ids),
        "programs_with_evidence": len(covered),
        "evidence_total": len(rows),
        "by_status": by_status,
        "by_confidence": by_confidence,
        "by_authority": by_authority,
        "stale_count": stale,
        "unknowns": unknowns,
        "conflicting_count": by_status.get(EvidenceStatus.CONFLICTING.value, 0),
    }


@router.get("/strategies/{strategy_id}/fit")
async def get_fit_scores(
    strategy_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    strategy = await _get_strategy(session, strategy_id)
    rows = await strategies_repo.get_strategy_fits(
        session, strategy.profile_id, strategy.research_plan_id
    )
    return {
        "scoring_version": strategy.scoring_version,
        "items": [
            {
                "program_id": str(f.program_id),
                "overall_score": str(f.overall_score),
                "explanation": f.explanation,
            }
            for f in rows
        ],
    }


@router.post("/strategies/{strategy_id}/export/pdf")
async def export_strategy_pdf(
    strategy_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> Any:
    from fastapi.responses import Response

    from app.services.export.pdf import render_strategy_pdf

    strategy = await _get_strategy(session, strategy_id)
    payload = {
        "strategy_id": str(strategy.id),
        "created_at": strategy.created_at.isoformat(),
        "plan_health_score": (
            str(strategy.plan_health_score) if strategy.plan_health_score is not None else None
        ),
        "summary": strategy.summary,
        "scoring_version": strategy.scoring_version,
        "portfolio": await _portfolio_rows(session, strategy),
        "risks": await _risk_rows(session, strategy.profile_id),
        "roadmap_tasks": await _task_rows(session, strategy_id),
    }
    pdf_bytes = render_strategy_pdf(payload)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="admitgraph-strategy-{strategy_id}.pdf"'
        },
    )


_SIMULATION_TOP_PRIORITY = 3


async def _simulation_base(
    session: AsyncSession, strategy: StrategyRun, profile: StudentProfile
) -> dict[str, Any]:
    """Snapshot a counterfactual starts from: profile facts + strategy context."""
    prefs = await strategies_repo.get_profile_preferences(session, profile.id)
    english = await strategies_repo.get_latest_english_score(session, profile.id)
    ielts_score: float | None = None
    english_score: float | None = None
    english_type: str | None = None
    if english is not None and english.overall_score is not None:
        english_score = float(english.overall_score)
        english_type = english.test_type
        if english.test_type.upper() in ("IELTS", "IELTS_ACADEMIC"):
            ielts_score = english_score
    plans = await strategies_repo.list_strategy_plans(session, strategy.id)
    category_rank = {"LOWER_RISK": 0, "TARGET": 1, "REACH": 2}
    top_plans = sorted(
        plans, key=lambda p: (category_rank.get(p.category.value, 9), p.priority)
    )[:_SIMULATION_TOP_PRIORITY]
    return {
        "total_budget_amount": (
            float(profile.total_budget_amount) if profile.total_budget_amount else None
        ),
        "budget_currency": profile.budget_currency,
        "cgpa": float(profile.cgpa) if profile.cgpa is not None else None,
        "cgpa_scale": float(profile.cgpa_scale) if profile.cgpa_scale is not None else None,
        "percentage": float(profile.percentage) if profile.percentage is not None else None,
        "backlogs": profile.backlogs or 0,
        "graduation_year": profile.graduation_year,
        "ielts_overall": ielts_score,
        "english_test_overall": english_score,
        "english_test_type": english_type,
        "preferred_countries": list((prefs.preferred_countries if prefs else None) or []),
        "top_priority_program_ids": [str(p.program_id) for p in top_plans],
    }


@router.post("/strategies/{strategy_id}/simulate")
async def simulate(
    strategy_id: uuid.UUID, payload: dict[str, Any], session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """Run a counterfactual: modify the profile, then re-tier what is known.

    `portfolio_before` is this strategy's stored ApplicationPlan rows;
    `portfolio_after` re-scores the strategy's own candidate programs with the
    real scoring/selection helpers under the modified profile (a pure
    computation — no plan rows are written and no research run is started);
    `delta` diffs the two and explains the move in one sentence. The persisted
    CounterfactualRun row (and its `counterfactual_run_id`) is unchanged.
    """
    scenario = str(payload.get("scenario", "CUSTOM"))
    if scenario not in SCENARIOS:
        raise AppError(422, "VALIDATION_ERROR", f"Unknown scenario. Allowed: {sorted(SCENARIOS)}")
    strategy = await _get_strategy(session, strategy_id)
    profile = await get_or_create_profile(session)
    base = await _simulation_base(session, strategy, profile)
    modified = apply_scenario(base, scenario, payload.get("modifications"))
    before = await _portfolio_rows(session, strategy)
    after, scored = await recompute_portfolio(session, strategy, modified)
    delta = compute_delta(before, after, scenario=scenario, candidates_scored=scored)
    run = CounterfactualRun(
        profile_id=profile.id,
        base_strategy_run_id=strategy.id,
        scenario_name=scenario,
        modified_profile=modified,
        result={
            "scenario": scenario,
            "modified_budget": modified.get("total_budget_amount"),
            "portfolio_before": len(before),
            "portfolio_after": len(after),
            "delta_summary": delta["summary"],
        },
    )
    await strategies_repo.save_counterfactual_run(session, run)
    return {
        "counterfactual_run_id": str(run.id),
        "scenario": scenario,
        "modified_profile": modified,
        "portfolio_before": before,
        "portfolio_after": after,
        "delta": delta,
    }
