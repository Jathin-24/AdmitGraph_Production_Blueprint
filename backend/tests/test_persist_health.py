"""Plan health (MASTER_SPEC §17): pure score formula + real DB-backed inputs.

The formula tests pin the weights; the DB tests prove `plan_health_inputs`
feeds it real data (blockers, conflicting/stale evidence, next deadline,
document readiness) instead of a hard-coded perfect score.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.services.strategy.health import plan_health_score

_TODAY = date(2026, 10, 8)


def _score(**overrides: Any) -> Decimal:
    base: dict[str, Any] = {
        "blocker_count": 0,
        "unresolved_conflicts": 0,
        "stale_evidence_count": 0,
        "nearest_deadline": None,
        "financial_feasible": True,
        "documents_ready": True,
        "today": _TODAY,
    }
    base.update(overrides)
    return plan_health_score(**base)


# ------------------------------------------------------------------- formula
def test_all_clear_inputs_score_full_marks() -> None:
    assert _score() == Decimal("100.00")


def test_each_real_input_reduces_the_score() -> None:
    assert _score(blocker_count=1) == Decimal("80.00")  # -20 per blocker, capped at 40
    assert _score(blocker_count=3) == Decimal("60.00")
    assert _score(unresolved_conflicts=1) == Decimal("90.00")  # -10 each, capped at 20
    assert _score(stale_evidence_count=1) == Decimal("95.00")  # -5 each, capped at 15
    assert _score(documents_ready=False) == Decimal("90.00")
    assert _score(financial_feasible=False) == Decimal("80.00")


def test_deadline_proximity_is_weighted_by_urgency() -> None:
    assert _score(nearest_deadline=_TODAY + timedelta(days=7)) == Decimal("90.00")
    assert _score(nearest_deadline=_TODAY + timedelta(days=60)) == Decimal("100.00")
    # An already-passed deadline is the strongest timing signal.
    assert _score(nearest_deadline=_TODAY - timedelta(days=1)) == Decimal("70.00")


def test_score_is_clamped_at_zero() -> None:
    assert (
        _score(
            blocker_count=5,
            unresolved_conflicts=5,
            stale_evidence_count=5,
            nearest_deadline=_TODAY - timedelta(days=3),
            financial_feasible=False,
            documents_ready=False,
        )
        == Decimal("0.00")
    )


# ---------------------------------------------------------------- DB inputs
async def test_empty_portfolio_inputs_carry_no_penalties(db_session: Any) -> None:
    from app.services.profile import get_or_create_profile
    from app.services.strategy.persist import plan_health_inputs

    profile = await get_or_create_profile(db_session)
    health = await plan_health_inputs(db_session, profile.id, set())

    assert health["blocker_count"] == 0
    assert health["unresolved_conflicts"] == 0
    assert health["stale_evidence_count"] == 0
    assert health["nearest_deadline"] is None
    assert health["financial_feasible"] is True
    # Clean inputs (documents aside) produce a perfect score: nothing is invented.
    assert plan_health_score(**{**health, "documents_ready": True}) == Decimal("100.00")


async def test_populated_portfolio_inputs_lower_the_score(db_session: Any) -> None:
    from app.db.models import (
        ConfidenceLevel,
        Evidence,
        EvidenceStatus,
        Institution,
        Intake,
        Program,
        Requirement,
        RequirementStatus,
        Source,
        SourceAuthority,
    )
    from app.services.profile import get_or_create_profile
    from app.services.strategy.persist import plan_health_inputs

    now = datetime.now(UTC)
    profile = await get_or_create_profile(db_session)
    suffix = uuid.uuid4().hex[:8]
    institution = Institution(
        canonical_name=f"Health University {suffix}",
        normalized_name=f"health university {suffix}",
        domain=f"health-{suffix}.example.edu",
    )
    db_session.add(institution)
    await db_session.flush()
    program = Program(
        institution_id=institution.id,
        canonical_name=f"M.Sc. Health Probe {suffix}",
        normalized_name=f"m.sc. health probe {suffix}",
    )
    db_session.add(program)
    await db_session.flush()

    # A mandatory requirement the profile cannot satisfy -> blocker.
    db_session.add(
        Requirement(
            program_id=program.id,
            requirement_type="academic",
            title="CGPA Min",
            normalized_key="cgpa_min",
            operator="gte",
            value={"min": 9.8, "scale": "10"},
            mandatory=True,
            status=RequirementStatus.NOT_SATISFIED,
        )
    )
    source = Source(
        url=f"https://www.health-{suffix}.example.edu/ai",
        canonical_url=f"https://www.health-{suffix}.example.edu/ai-{suffix}",
        domain=f"health-{suffix}.example.edu",
        title="Health University AI",
        source_authority=SourceAuthority.CREDIBLE_SECONDARY,
        last_seen_at=now,
    )
    db_session.add(source)
    await db_session.flush()

    def _claim(value: str, *, status: EvidenceStatus, retrieved: datetime, stale: bool) -> Evidence:
        return Evidence(
            source_id=source.id,
            claim_type="deadline",
            subject_type="program",
            subject_id=program.id,
            claim=f"Deadline claim {value}",
            normalized_claim="application_deadline",
            extracted_value={"date": value},
            confidence=ConfidenceLevel.HIGH,
            status=status,
            retrieved_at=retrieved,
            freshness_deadline=(
                now - timedelta(days=30) if stale else retrieved + timedelta(days=7)
            ),
        )

    db_session.add(
        _claim("2027-01-15", status=EvidenceStatus.CONFLICTING, retrieved=now, stale=False)
    )
    db_session.add(
        _claim(
            "2027-02-01",
            status=EvidenceStatus.CONFLICTING,
            retrieved=now - timedelta(days=3),
            stale=False,
        )
    )
    db_session.add(
        _claim(
            "2026-06-01",
            status=EvidenceStatus.STALE,
            retrieved=now - timedelta(days=120),
            stale=True,
        )
    )
    db_session.add(
        Intake(
            program_id=program.id,
            intake_label="Winter 2027",
            intake_year=2027,
            application_deadline=date.today() + timedelta(days=7),
            deadline_type="application",
        )
    )
    await db_session.commit()

    health = await plan_health_inputs(db_session, profile.id, {program.id})

    assert health["blocker_count"] == 1
    assert health["unresolved_conflicts"] == 2  # both CONFLICTING rows
    assert health["stale_evidence_count"] >= 1
    assert health["nearest_deadline"] is not None
    assert health["financial_feasible"] is True  # tuition UNKNOWN -> not infeasible
    # Real penalties replace the old hard-coded 100.
    assert plan_health_score(**health) < Decimal("100.00")


async def test_core_documents_must_all_be_done(db_session: Any) -> None:
    from app.db.models import Document, TaskStatus
    from app.services.profile import get_or_create_profile
    from app.services.strategy.persist import CORE_DOCUMENT_TYPES, documents_ready

    profile = await get_or_create_profile(db_session)
    # Deterministic baseline: drop core-type rows other tests may have created.
    rows = (
        (await db_session.execute(select(Document).where(Document.profile_id == profile.id)))
        .scalars()
        .all()
    )
    for row in rows:
        if row.document_type.strip().lower().replace(" ", "_") in CORE_DOCUMENT_TYPES:
            await db_session.delete(row)
    await db_session.commit()

    assert await documents_ready(db_session, profile.id) is False

    for doc_type in CORE_DOCUMENT_TYPES:
        db_session.add(
            Document(
                profile_id=profile.id,
                document_type=doc_type,
                status=TaskStatus.DONE,
            )
        )
    await db_session.commit()
    assert await documents_ready(db_session, profile.id) is True


async def test_roadmap_tasks_derive_only_from_existing_rows(db_session: Any) -> None:
    """Roadmap = review task + real intake deadlines + unsatisfied requirements.

    Deadline dates come from the intake rows; requirement tasks carry no
    invented due dates. Everything is scoped to this run's strategy.
    """
    from app.db.models import (
        ApplicationPlan,
        FitAssessment,
        Institution,
        Intake,
        Program,
        Requirement,
        RequirementStatus,
        RoadmapTask,
    )
    from app.services.profile import get_or_create_profile
    from app.services.strategy.persist import step_build_strategy

    profile = await get_or_create_profile(db_session)
    suffix = uuid.uuid4().hex[:8]
    institution = Institution(
        canonical_name=f"Roadmap University {suffix}",
        normalized_name=f"roadmap university {suffix}",
        domain=f"roadmap-{suffix}.example.edu",
    )
    db_session.add(institution)
    await db_session.flush()
    program = Program(
        institution_id=institution.id,
        canonical_name=f"M.Sc. Roadmap Probe {suffix}",
        normalized_name=f"m.sc. roadmap probe {suffix}",
    )
    db_session.add(program)
    await db_session.flush()

    deadline = date.today() + timedelta(days=60)
    db_session.add(
        Intake(
            program_id=program.id,
            intake_label="Winter 2027",
            intake_year=2027,
            application_deadline=deadline,
            deadline_type="application",
        )
    )
    db_session.add(
        Requirement(
            program_id=program.id,
            requirement_type="academic",
            title="Roadmap CGPA Min",
            normalized_key="roadmap_cgpa_min",
            operator="gte",
            value={"min": 9.9, "scale": "10"},
            mandatory=True,
            status=RequirementStatus.NOT_SATISFIED,
        )
    )
    db_session.add(
        FitAssessment(
            profile_id=profile.id,
            research_plan_id=None,
            program_id=program.id,
            overall_score=Decimal("55.00"),
            scoring_version="v1",
        )
    )
    await db_session.commit()

    built = await step_build_strategy(db_session, profile.id, None)
    strategy_id = uuid.UUID(built["strategy_run_id"])
    tasks = (
        (
            await db_session.execute(
                select(RoadmapTask).where(RoadmapTask.strategy_run_id == strategy_id)
            )
        )
        .scalars()
        .all()
    )
    assert built["roadmap_tasks_created"] == len(tasks)
    assert any(t.title == "Review top program requirements" for t in tasks)
    deadline_tasks = [t for t in tasks if t.task_type == "DEADLINE"]
    requirement_tasks = [t for t in tasks if t.task_type == "REQUIREMENT"]
    assert deadline_tasks, "future intake deadlines must yield DEADLINE tasks"
    assert any(t.due_date is not None for t in deadline_tasks)
    assert requirement_tasks, "unsatisfied mandatory requirements must yield tasks"
    # Only deadlines get due dates — no invented dates for the rest.
    assert all(t.due_date is None for t in requirement_tasks)
    # The portfolio card carries the same grounded intake deadline (None for
    # programs whose deadlines were never extracted).
    plans = (
        (
            await db_session.execute(
                select(ApplicationPlan).where(ApplicationPlan.strategy_run_id == strategy_id)
            )
        )
        .scalars()
        .all()
    )
    own_plans = [p for p in plans if p.program_id == program.id]
    assert own_plans, "the scored program must occupy a portfolio slot"
    assert own_plans[0].next_deadline == deadline
