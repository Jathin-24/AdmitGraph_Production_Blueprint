"""Integration tests against real PostgreSQL (fixture: tests/conftest.py).

These exercise repositories, conflict detection, the strategy persistence steps
and the full research orchestrator with a fake SerpApi client (no network) and
LLM calls disabled (honest no-claims path).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.db.models import (
    ApplicationPlan,
    ConfidenceLevel,
    Evidence,
    EvidenceConflictMember,
    EvidenceStatus,
    FitAssessment,
    Institution,
    Program,
    Requirement,
    RequirementStatus,
    ResearchPlan,
    ResearchPlanStep,
    RunStatus,
    SearchResult,
    SearchRun,
    Source,
    SourceAuthority,
    StrategyRun,
)
from app.services.profile import get_or_create_profile


class FakeSerpApi:
    """Deterministic SerpApi stand-in returning university-like results."""

    async def search(
        self,
        engine: str,
        q: str,
        *,
        parameters: dict[str, Any] | None = None,
        cache: dict[str, dict[str, Any]] | None = None,
    ) -> Any:
        from app.services.serpapi.client import SerpApiResult

        organic: list[dict[str, Any]] = []
        if engine == "google" and "admission requirements" in q:
            organic = [
                {
                    "title": "M.Sc. Artificial Intelligence - Technical University of Munich",
                    "link": "https://www.tum.de/en/studies/degree-programs/ai",
                    "snippet": "Applicants require a minimum CGPA of 3.0 on a 4.0 scale.",
                    "displayed_link": "www.tum.de › studies › ai",
                },
                {
                    "title": "Computer Science M.Sc. - University of Stuttgart",
                    "link": "https://www.uni-stuttgart.de/study/programmes/cs",
                    "snippet": "Application deadline for the winter semester is 15 January.",
                    "displayed_link": "www.uni-stuttgart.de › study › cs",
                },
            ]
        return SerpApiResult(
            engine=engine,
            query=q,
            parameters=parameters or {},
            raw={},
            organic_results=organic,
            search_id=f"fake-{engine}",
            duration_ms=1,
        )


async def test_plan_creation_and_idempotency(db_session: Any) -> None:
    from app.services.research.orchestrator import ResearchService

    profile = await get_or_create_profile(db_session)
    service = ResearchService()
    first = await service.create_plan(
        db_session, profile.id, {"intake_year": 2027}, idempotency_key="same-key"
    )
    second = await service.create_plan(
        db_session, profile.id, {"intake_year": 2027}, idempotency_key="same-key"
    )
    assert first.id == second.id
    steps = (
        await db_session.execute(
            select(ResearchPlanStep).where(ResearchPlanStep.research_plan_id == first.id)
        )
    ).scalars().all()
    assert len(steps) == 9
    assert {s.step_key for s in steps} >= {"extract_evidence", "score_fit", "build_strategy"}


async def test_evidence_recorded_with_provenance_and_conflicts(db_session: Any) -> None:
    from app.services.evidence.conflicts import ConflictDetectionService
    from app.services.evidence.extraction import EvidenceExtractionService, ExtractedClaim

    now = datetime.now(UTC)
    source = Source(
        url="https://www.tum.de/ai",
        canonical_url=f"https://www.tum.de/ai-{uuid.uuid4().hex[:6]}",
        domain="www.tum.de",
        title="TU Munich",
        source_authority=SourceAuthority.CREDIBLE_SECONDARY,
        last_seen_at=now,
    )
    db_session.add(source)
    await db_session.flush()
    run = SearchRun(engine="google", query="q", parameters={}, status=RunStatus.SUCCEEDED)
    db_session.add(run)
    await db_session.flush()
    result = SearchResult(
        search_run_id=run.id,
        source_id=source.id,
        position=1,
        result_type="google",
        title="M.Sc. AI",
        snippet="Deadline is 15 January.",
        result_url="https://www.tum.de/ai",
        raw_payload={},
        retrieved_at=now,
    )
    db_session.add(result)
    await db_session.flush()

    program_id = uuid.uuid4()
    service = EvidenceExtractionService()
    stored = await service.record_claims(
        db_session,
        source,
        result,
        [
            ExtractedClaim(
                claim_type="deadline",
                normalized_key="application_deadline",
                value={"date": "2027-01-15"},
                claim="Page says 15 January.",
                confidence=ConfidenceLevel.HIGH,
                subject_id=program_id,
            ),
            ExtractedClaim(
                claim_type="deadline",
                normalized_key="application_deadline",
                value={"date": "2027-02-01"},
                claim="Other page says 1 February.",
                confidence=ConfidenceLevel.MEDIUM,
                subject_id=program_id,
            ),
        ],
    )
    assert len(stored) == 2
    row = await db_session.get(Evidence, stored[0].id)
    assert row is not None
    assert row.source_id == source.id
    assert row.search_result_id == result.id
    assert row.extraction_version == "v1"
    assert row.retrieved_at is not None

    conflicts = await ConflictDetectionService().detect_for_subject(db_session, "program", program_id)
    assert len(conflicts) == 1
    members = (
        await db_session.execute(
            select(EvidenceConflictMember).where(EvidenceConflictMember.conflict_id == conflicts[0].id)
        )
    ).scalars().all()
    assert len(members) == 2
    statuses = (
        await db_session.execute(select(Evidence.status).where(Evidence.subject_id == program_id))
    ).scalars().all()
    assert all(s == EvidenceStatus.CONFLICTING for s in statuses)


async def test_strategy_persistence_steps_end_to_end(db_session: Any) -> None:
    from app.services.strategy.persist import (
        step_assess_risks,
        step_build_strategy,
        step_evaluate_requirements,
        step_score_fit,
    )

    profile = await get_or_create_profile(db_session)
    profile.cgpa = 8.1
    profile.cgpa_scale = 10
    await db_session.commit()

    institution = Institution(canonical_name="TU Munich", normalized_name="tu munich", domain="tum.de")
    db_session.add(institution)
    await db_session.flush()
    program = Program(
        institution_id=institution.id,
        canonical_name="M.Sc. Artificial Intelligence",
        normalized_name="m.sc. artificial intelligence",
    )
    db_session.add(program)
    await db_session.flush()
    db_session.add(
        Requirement(
            program_id=program.id,
            requirement_type="academic",
            title="CGPA Min",
            normalized_key="cgpa_min",
            operator="gte",
            value={"min": 3.0, "scale": "4.0"},
            mandatory=True,
            status=RequirementStatus.UNKNOWN,
        )
    )
    await db_session.commit()

    evaluated = await step_evaluate_requirements(db_session, profile.id)
    assert evaluated["requirements_evaluated"] >= 1
    req = (
        await db_session.execute(select(Requirement).where(Requirement.program_id == program.id))
    ).scalar_one()
    assert req.status == RequirementStatus.SATISFIED  # 81% >= 75%

    scored = await step_score_fit(db_session, profile.id, None)
    assert scored["fit_assessments_created"] >= 1

    # Explanations are user-facing: human-readable copy, never raw dicts,
    # and always restating that fit is not an admission probability.
    fit = (await db_session.execute(select(FitAssessment))).scalars().first()
    assert fit is not None and fit.explanation
    assert not fit.explanation.startswith("subscores=")
    assert "not an admission probability" in fit.explanation

    risk_out = await step_assess_risks(db_session, profile.id)
    assert "risks_created" in risk_out

    built = await step_build_strategy(db_session, profile.id, None)
    strategy_id = uuid.UUID(built["strategy_run_id"])
    strategy = await db_session.get(StrategyRun, strategy_id)
    assert strategy is not None
    assert strategy.status == RunStatus.SUCCEEDED
    plans = (
        await db_session.execute(
            select(ApplicationPlan).where(ApplicationPlan.strategy_run_id == strategy_id)
        )
    ).scalars().all()
    assert plans, "portfolio must contain at least one application plan"
    # The scratch DB is shared across tests, so the portfolio may also include
    # programs created by other tests — this one must be among them.
    assert program.id in {p.program_id for p in plans}


async def test_full_orchestrator_run_with_fake_search(db_session: Any) -> None:
    from app.services.research.orchestrator import ResearchService

    profile = await get_or_create_profile(db_session)
    # Onboarding normally fills these; a bare profile has no country to search,
    # so the planner would emit zero discovery queries.
    profile.institution_country_code = "DE"
    await db_session.commit()
    service = ResearchService(serpapi=FakeSerpApi())
    plan = await service.create_plan(db_session, profile.id, {"intake_year": 2027})
    plan_id = plan.id
    await service.execute_plan(plan_id)

    db_session.expire_all()
    fresh = await db_session.get(ResearchPlan, plan_id)
    assert fresh is not None
    assert fresh.status == RunStatus.SUCCEEDED
    steps = (
        await db_session.execute(
            select(ResearchPlanStep).where(ResearchPlanStep.research_plan_id == plan_id)
        )
    ).scalars().all()
    by_key = {s.step_key: s for s in steps}
    assert by_key["discovery_search"].status == RunStatus.SUCCEEDED
    assert by_key["normalize_programs"].status == RunStatus.SUCCEEDED
    assert by_key["build_strategy"].status == RunStatus.SUCCEEDED
    assert by_key["normalize_programs"].output is not None
    assert by_key["normalize_programs"].output.get("programs_created", 0) >= 1

    programs = (await db_session.execute(select(Program))).scalars().all()
    assert any(
        p.normalized_name.startswith("m.sc.") or "computer science" in p.normalized_name
        for p in programs
    )

    strategies = (
        await db_session.execute(select(StrategyRun).where(StrategyRun.research_plan_id == plan_id))
    ).scalars().all()
    assert len(strategies) == 1
