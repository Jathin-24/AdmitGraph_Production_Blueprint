"""Demo research run: replays a captured real run end-to-end in seconds.

POST /research/demo creates a ResearchPlan with mode='demo' and the same nine
step keys as the live orchestrator, then runs them in a background task:

- validate_profile / plan_queries: the REAL profile + planner logic.
- discovery_search / normalize_programs / extract_evidence: replayed from the
  captured fixture (scripts/demo/example.json) — zero SerpApi, zero LLM calls.
- evaluate_requirements / score_fit / assess_risks / build_strategy: the REAL
  strategy persist steps, so fit scores, portfolio, risks and roadmap are
  genuinely computed for this participant's profile.

The run notices CANCELLED between steps, commits per step (the events endpoint
streams progress), and emits `research.run_finished` when done.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import AppError
from app.db.models import (
    ProfilePreference,
    ResearchPlan,
    ResearchPlanStep,
    RunStatus,
    StudentProfile,
)
from app.db.session import get_engine
from app.services.demo.fixture import apply_fixture, load_fixture
from app.services.demo.persona import apply_demo_persona
from app.services.onboarding import REQUIRED_KEYS
from app.services.profile import validate_profile
from app.services.research.planner import plan_queries

logger = logging.getLogger(__name__)

# Pause between steps so the UI can stream progress; tests set this to 0.
STEP_DELAY_SECONDS = 0.4

# Mirrors ResearchService.create_plan exactly (step key, service name).
DEMO_STEPS: list[tuple[str, str]] = [
    ("validate_profile", "profile"),
    ("plan_queries", "planner"),
    ("discovery_search", "serpapi"),
    ("normalize_programs", "discovery"),
    ("extract_evidence", "evidence"),
    ("evaluate_requirements", "matching"),
    ("score_fit", "scoring"),
    ("assess_risks", "risk"),
    ("build_strategy", "strategy"),
]

_background_tasks: set[asyncio.Task[None]] = set()
# One in-flight plan creation per event loop: concurrent POSTs must not race
# past the "already queued/running" check and start a second demo run.
_create_locks: dict[asyncio.AbstractEventLoop, asyncio.Lock] = {}


def _creation_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _create_locks.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _create_locks[loop] = lock
    return lock


async def start_demo_run(session: AsyncSession) -> ResearchPlan:
    """Create (or return the in-flight) demo plan for the requesting profile."""
    from app.services.profile import get_or_create_profile

    async with _creation_lock():
        # Profile creation sits inside the lock too: two concurrent first posts
        # from a brand-new participant must not race the unique user_id key.
        profile = await get_or_create_profile(session)
        missing_required = [
            key for key in sorted(REQUIRED_KEYS) if getattr(profile, key) in (None, "")
        ]
        persona_applied = bool(missing_required)
        if persona_applied:
            await apply_demo_persona(session, profile)

        # One demo run at a time per profile: a second click returns the same run.
        existing = (
            await session.execute(
                select(ResearchPlan)
                .where(
                    ResearchPlan.profile_id == profile.id,
                    ResearchPlan.mode == "demo",
                    ResearchPlan.status.in_((RunStatus.QUEUED, RunStatus.RUNNING)),
                )
                .order_by(ResearchPlan.created_at.desc())
            )
        ).scalars().first()
        if existing is not None:
            return existing

        # Fail fast (clear 503) if the captured fixture is missing/invalid.
        load_fixture()

        plan = ResearchPlan(
            profile_id=profile.id,
            requested_goal={
                "demo": True,
                "intake_year": datetime.now(UTC).year + 1,
                "persona_applied": persona_applied,
            },
            status=RunStatus.QUEUED,
            mode="demo",
        )
        session.add(plan)
        await session.flush()
        for key, service in DEMO_STEPS:
            session.add(
                ResearchPlanStep(research_plan_id=plan.id, step_key=key, service_name=service)
            )
        await session.commit()
        await session.refresh(plan)

    dispatch_demo_plan(plan.id)
    return plan


def dispatch_demo_plan(plan_id: uuid.UUID) -> None:
    task = asyncio.create_task(execute_demo_plan(plan_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    def _finished(t: asyncio.Task[None]) -> None:
        from app.core.events import emit  # local: keeps runner import-light

        emit("research.run_finished", plan_id=plan_id, task=t)

    task.add_done_callback(_finished)


async def execute_demo_plan(plan_id: uuid.UUID) -> None:
    """Runs the demo plan end-to-end; never raises (done-callbacks must fire)."""
    get_engine()
    maker = async_sessionmaker(get_engine(), expire_on_commit=False)
    try:
        async with maker() as session:
            plan = await session.get(ResearchPlan, plan_id)
            if plan is None:
                return
            if plan.status == RunStatus.CANCELLED:
                plan.completed_at = datetime.now(UTC)
                await session.commit()
                return
            plan.status = RunStatus.RUNNING
            plan.started_at = datetime.now(UTC)
            await session.commit()

            failures = 0
            failure_message: str | None = None
            try:
                failures = await _run_steps(session, plan)
            except Exception as exc:  # noqa: BLE001 - background task must not crash
                logger.exception("demo plan %s failed", plan_id)
                failure_message = str(exc)[:1000]

            # Re-read status: a cancel may have landed after the last step.
            await session.refresh(plan)
            if plan.status != RunStatus.CANCELLED:
                if failure_message is not None:
                    plan.status = RunStatus.FAILED
                    plan.error_message = failure_message
                elif failures:
                    plan.status = RunStatus.PARTIAL
                else:
                    plan.status = RunStatus.SUCCEEDED
            plan.completed_at = datetime.now(UTC)
            await session.commit()
    except Exception:  # noqa: BLE001 - last resort: log, never raise from a task
        logger.exception("could not finalize demo plan %s", plan_id)


async def _run_steps(session: AsyncSession, plan: ResearchPlan) -> int:
    """Execute the nine steps in order; returns the number of failed steps."""
    result = await session.execute(
        select(ResearchPlanStep).where(ResearchPlanStep.research_plan_id == plan.id)
    )
    by_key = {s.step_key: s for s in result.scalars().all()}
    steps = [by_key[key] for key, _ in DEMO_STEPS if key in by_key]
    goal = plan.requested_goal if isinstance(plan.requested_goal, dict) else {}
    persona_applied = bool(goal.get("persona_applied"))

    failures = 0
    for step in steps:
        # Cancellation is noticed between steps: stop without touching the step.
        await session.refresh(plan)
        if plan.status == RunStatus.CANCELLED:
            return failures
        step.status = RunStatus.RUNNING
        step.started_at = datetime.now(UTC)
        await session.commit()
        if STEP_DELAY_SECONDS > 0:
            await asyncio.sleep(STEP_DELAY_SECONDS)
        try:
            step.output = await _execute_step(session, plan, step.step_key, persona_applied)
            step.status = RunStatus.SUCCEEDED
        except Exception as exc:  # noqa: BLE001 - one bad step must not kill the run
            logger.exception("demo step %s failed", step.step_key)
            step.status = RunStatus.FAILED
            step.error_message = (str(exc) or repr(exc))[:1000]
            failures += 1
        step.completed_at = datetime.now(UTC)
        await session.commit()
    return failures


async def _execute_step(
    session: AsyncSession, plan: ResearchPlan, step_key: str, persona_applied: bool
) -> dict[str, Any]:
    profile = await session.get(StudentProfile, plan.profile_id)
    if profile is None:
        raise AppError(500, "PROFILE_MISSING", "Profile for the demo run no longer exists")

    if step_key == "validate_profile":
        result = validate_profile(profile)
        if not result.valid:
            raise ValueError("; ".join(i.message for i in result.issues))
        return {"valid": True, "demo": True, "persona_applied": persona_applied}

    if step_key == "plan_queries":
        prefs = (
            await session.execute(
                select(ProfilePreference).where(ProfilePreference.profile_id == profile.id)
            )
        ).scalars().first()
        prefs_dict = {"preferred_countries": prefs.preferred_countries if prefs else []}
        goal = plan.requested_goal if isinstance(plan.requested_goal, dict) else {}
        year = int(goal.get("intake_year", datetime.now(UTC).year + 1))
        queries = plan_queries(profile, prefs_dict, year)
        # Canonical serialization (same as the live runner): keeps template,
        # budget and per-query `parameters` (e.g. discovery `gl`) so a demo
        # run's stored plan reads exactly like a live one.
        plan.planned_queries = [q.as_dict() for q in queries]
        await session.commit()
        return {"queries": [q.q for q in queries], "demo": True}

    if step_key in ("discovery_search", "normalize_programs", "extract_evidence"):
        counts = await apply_fixture(session, load_fixture())
        if step_key == "discovery_search":
            return {
                "fixture": True,
                "programs": counts["programs"],
                "results_stored": counts["results_stored"],
                "sources": counts["sources"],
                "searches_run": counts["searches_run"],
            }
        if step_key == "normalize_programs":
            created = counts["created"]["programs"]
            return {
                "fixture": True,
                "programs": counts["programs"],
                "programs_created": created,
            }
        return {
            "fixture": True,
            "claims_created": counts["created"]["evidence"],
            "claims_replayed": counts["evidence"],
            "requirements_synced": counts["requirements"],
        }

    from app.services.strategy.persist import (
        step_assess_risks,
        step_build_strategy,
        step_evaluate_requirements,
        step_score_fit,
    )

    if step_key == "evaluate_requirements":
        return await step_evaluate_requirements(session, profile.id)
    if step_key == "score_fit":
        return await step_score_fit(session, profile.id, plan.id)
    if step_key == "assess_risks":
        return await step_assess_risks(session, profile.id)
    if step_key == "build_strategy":
        return await step_build_strategy(session, profile.id, plan.id)
    raise ValueError(f"Unknown demo step {step_key}")
