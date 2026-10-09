"""Demo endpoint + replay integration tests against real PostgreSQL.

POST /research/demo must give any participant the full dashboard experience
from the captured real run in seconds, with zero SerpApi/LLM calls: a
mode='demo' plan with the orchestrator's nine steps, real strategy persist
steps computing portfolio/fit/risks, `research.run_finished` emitted with the
real Task, single-run dedupe for concurrent posts, and cancel-between-steps.
Every test wipes its own rows so later suites see a clean database.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_token
from app.db.models import (
    ApplicationPlan,
    Country,
    Evidence,
    Experience,
    FitAssessment,
    Institution,
    ProfilePreference,
    Program,
    Requirement,
    ResearchPlan,
    Risk,
    RoadmapTask,
    Source,
    StrategyRun,
    StudentProfile,
    User,
)

# Aliased: pytest must not try to collect the SQLAlchemy model `TestScore` as a
# test class ("cannot collect test class 'TestScore' because it has a __init__").
from app.db.models import TestScore as StudentTestScore
from app.services.demo import runner as demo_runner
from app.services.demo.fixture import _evidence_hash, load_fixture
from app.services.demo.persona import IELTS_TEST_TYPE, apply_demo_persona
from app.services.demo.runner import DEMO_STEPS
from app.services.onboarding import ENGLISH_TEST_TYPE

TERMINAL = ("SUCCEEDED", "FAILED", "PARTIAL", "CANCELLED")


@dataclass
class DemoEnv:
    session: AsyncSession
    user: User
    headers: dict[str, str]
    user_id: uuid.UUID


@pytest.fixture
async def api(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


@pytest.fixture
async def demo_env(db_session: AsyncSession) -> AsyncIterator[DemoEnv]:
    """A dedicated participant (own user/profile/plan) plus teardown.

    The bearer token routes requests to this user through the same middleware
    the frontend uses; teardown wipes everything the tests created so the
    shared scratch database stays clean for the other suites.
    """
    user = User(
        email=f"demo-it-{uuid.uuid4().hex[:8]}@test.local",
        full_name="Demo Integration Student",
    )
    db_session.add(user)
    await db_session.commit()  # request sessions must see the committed user
    token = create_token(user.id, "STUDENT")
    user_id = user.id
    yield DemoEnv(
        session=db_session, user=user, user_id=user_id,
        headers={"Authorization": f"Bearer {token}"},
    )

    await _wipe_user_state(db_session, user_id)
    await _wipe_fixture_rows(db_session)


async def _wipe_user_state(session: AsyncSession, user_id: uuid.UUID) -> None:
    profile = (
        await session.execute(select(StudentProfile).where(StudentProfile.user_id == user_id))
    ).scalars().first()
    if profile is not None:
        pid = profile.id
        strategy_ids = [
            row.id
            for row in (
                await session.execute(select(StrategyRun).where(StrategyRun.profile_id == pid))
            ).scalars()
        ]
        if strategy_ids:
            await session.execute(
                delete(ApplicationPlan).where(ApplicationPlan.strategy_run_id.in_(strategy_ids))
            )
            await session.execute(
                delete(RoadmapTask).where(RoadmapTask.strategy_run_id.in_(strategy_ids))
            )
            await session.execute(delete(StrategyRun).where(StrategyRun.id.in_(strategy_ids)))
        await session.execute(delete(FitAssessment).where(FitAssessment.profile_id == pid))
        await session.execute(delete(Risk).where(Risk.profile_id == pid))
        # ResearchPlanStep cascades from the plan (FK ondelete CASCADE).
        await session.execute(delete(ResearchPlan).where(ResearchPlan.profile_id == pid))
        # preferences, test scores and experiences cascade from the profile.
        await session.execute(delete(StudentProfile).where(StudentProfile.id == pid))
    await session.execute(delete(User).where(User.id == user_id))
    await session.commit()


async def _wipe_fixture_rows(session: AsyncSession) -> None:
    """Remove rows the replay created (evidence first, then programs, sources)."""
    fixture = load_fixture()
    hashes = sorted({_evidence_hash(item) for item in fixture.evidence})
    if hashes:
        await session.execute(delete(Evidence).where(Evidence.content_hash.in_(hashes)))
    program_names = [item.normalized_name for item in fixture.programs]
    if program_names:
        program_ids = [
            row.id
            for row in (
                await session.execute(
                    select(Program).where(Program.normalized_name.in_(program_names))
                )
            ).scalars()
        ]
        if program_ids:
            await session.execute(delete(Program).where(Program.id.in_(program_ids)))
    institution_names = sorted({item.normalized_name for item in fixture.institutions})
    if institution_names:
        for inst in (
            await session.execute(
                select(Institution).where(Institution.normalized_name.in_(institution_names))
            )
        ).scalars():
            referenced = (
                await session.execute(
                    select(Program.id).where(Program.institution_id == inst.id).limit(1)
                )
            ).first()
            if referenced is None:
                await session.execute(delete(Institution).where(Institution.id == inst.id))
    urls = [item.canonical_url for item in fixture.sources]
    if urls:
        await session.execute(delete(Source).where(Source.canonical_url.in_(urls)))
    await session.commit()


async def _wait_for_terminal(api: AsyncClient, headers: dict[str, str], plan_id: uuid.UUID) -> dict:
    deadline = time.monotonic() + 30
    while True:
        response = await api.get(f"/api/v1/research/runs/{plan_id}", headers=headers)
        assert response.status_code == 200, response.text
        data = response.json()
        if data["status"] in TERMINAL:
            return data
        assert time.monotonic() < deadline, f"demo run stuck at {data['status']}"
        await asyncio.sleep(0.05)


async def _steps(api: AsyncClient, headers: dict[str, str], plan_id: uuid.UUID) -> list[dict]:
    response = await api.get(f"/api/v1/research/runs/{plan_id}/events", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["steps"]


async def test_demo_endpoint_replays_captured_run_end_to_end(
    demo_env: DemoEnv, api: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core import events

    monkeypatch.setattr(demo_runner, "STEP_DELAY_SECONDS", 0)

    finished: list[tuple[Any, Any]] = []

    async def listener(plan_id: Any = None, task: Any = None, **_: Any) -> None:
        finished.append((plan_id, task))

    events.on("research.run_finished", listener)

    response = await api.post("/api/v1/research/demo", headers=demo_env.headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"research_plan_id", "status"}
    assert body["status"] == "QUEUED"
    plan_id = uuid.UUID(body["research_plan_id"])

    # GET run carries mode="demo" and the nine orchestrator steps exist.
    detail = (await api.get(f"/api/v1/research/runs/{plan_id}", headers=demo_env.headers)).json()
    assert detail["mode"] == "demo"
    steps = await _steps(api, demo_env.headers, plan_id)
    assert len(steps) == 9
    assert {s["step_key"] for s in steps} == {key for key, _ in DEMO_STEPS}
    assert {s["service_name"] for s in steps} == {svc for _, svc in DEMO_STEPS}

    final = await _wait_for_terminal(api, demo_env.headers, plan_id)
    assert final["status"] == "SUCCEEDED", final["error_message"]
    assert final["mode"] == "demo"
    assert final["completed_at"] is not None
    assert final["planned_queries"], "real planner output must be stored on the run"

    steps = await _steps(api, demo_env.headers, plan_id)
    by_key = {s["step_key"]: s for s in steps}
    assert all(s["status"] == "SUCCEEDED" for s in steps), {
        k: s["error_message"] for k, s in by_key.items()
    }

    # Persona applied for a brand-new participant, reported for UI badging.
    validate_output = by_key["validate_profile"]["output"]
    assert validate_output["valid"] is True
    assert validate_output["persona_applied"] is True

    # Honest replay: no live searches, N real sources re-stored.
    fixture = load_fixture()
    discovery = by_key["discovery_search"]["output"]
    assert discovery["fixture"] is True
    assert discovery["searches_run"] == 0
    assert discovery["sources"] == len(fixture.sources)
    assert discovery["programs"] == len(fixture.programs)
    assert by_key["extract_evidence"]["output"]["claims_replayed"] == len(fixture.evidence)

    # The finished event carries the real Task object.
    await asyncio.sleep(0.1)
    emitted = [task for pid, task in finished if str(pid) == str(plan_id)]
    assert emitted, "research.run_finished was not emitted for the demo run"
    assert isinstance(emitted[0], asyncio.Task)

    # Real strategy compute for THIS participant: portfolio + fit explanations.
    demo_env.session.expire_all()
    profile = (
        await demo_env.session.execute(
            select(StudentProfile).where(StudentProfile.user_id == demo_env.user_id)
        )
    ).scalars().one()
    assert profile.current_degree == "B.Tech Computer Science"
    assert profile.cgpa == Decimal("8.1")
    strategies = (
        await demo_env.session.execute(
            select(StrategyRun).where(StrategyRun.research_plan_id == plan_id)
        )
    ).scalars().all()
    assert len(strategies) == 1
    assert strategies[0].status.value == "SUCCEEDED"
    portfolio = (
        await demo_env.session.execute(
            select(ApplicationPlan).where(ApplicationPlan.strategy_run_id == strategies[0].id)
        )
    ).scalars().all()
    assert len(portfolio) >= 1, "portfolio must contain at least one program"
    fits = (
        await demo_env.session.execute(
            select(FitAssessment).where(FitAssessment.research_plan_id == plan_id)
        )
    ).scalars().all()
    assert len(fits) >= 1
    assert all(f.explanation and "not an admission probability" in f.explanation for f in fits)

    # The participant got the English score + internship persona rows.
    scores = (
        await demo_env.session.execute(
            select(StudentTestScore).where(StudentTestScore.profile_id == profile.id)
        )
    ).scalars().all()
    assert {s.test_type for s in scores} == {IELTS_TEST_TYPE, ENGLISH_TEST_TYPE}
    assert all(s.overall_score == Decimal("7.5") for s in scores)
    experiences = (
        await demo_env.session.execute(
            select(Experience).where(Experience.profile_id == profile.id)
        )
    ).scalars().all()
    assert len(experiences) == 1
    assert "DEMO PERSONA" in (experiences[0].description or "")

    # Fixture replayed: every captured claim is in the database (counted by
    # captured row id — other suites' evidence must not be counted).
    fixture_ids = [uuid.UUID(item.id) for item in fixture.evidence]
    replayed = (
        await demo_env.session.execute(select(Evidence).where(Evidence.id.in_(fixture_ids)))
    ).scalars().all()
    assert len(replayed) == len(fixture.evidence)
    requirements = (
        await demo_env.session.execute(select(Requirement))
    ).scalars().all()
    assert len(requirements) >= len(fixture.requirements)

    # Demo plans serialize exactly like the live runner (as_dict) — checked on
    # the stored plan row, since the run API intentionally flattens the list
    # to query strings for display. Template, budget and per-query parameters
    # (discovery `gl`) all survive.
    plan_row = (
        await demo_env.session.execute(select(ResearchPlan).where(ResearchPlan.id == plan_id))
    ).scalars().one()
    stored = plan_row.planned_queries
    assert stored and set(stored[0]) == {
        "engine",
        "q",
        "purpose",
        "template",
        "parameters",
        "budget",
    }
    discovery_entries = [q for q in stored if q.get("purpose") == "discovery"]
    assert discovery_entries, "demo planner must emit discovery queries"
    assert all("gl" in (q.get("parameters") or {}) for q in discovery_entries)

    # The user-facing promise of audit D-2: after a demo run, Explore's
    # country filter actually returns the replayed programs (the catalog spans
    # several countries now, so ?country=DE must include every DE fixture
    # program, and non-DE fixture programs must not leak into the result).
    catalog = (
        await api.get("/api/v1/programs", params={"country": "DE", "page_size": 100})
    ).json()
    listed = {item["name"] for item in catalog["items"]}
    de_programs = [
        item for item in fixture.programs if (item.country_code or "").upper() == "DE"
    ]
    assert de_programs, "the fixture must keep at least one German program"
    assert {item.canonical_name for item in de_programs} <= listed, (
        "GET /programs?country=DE must return the demo-replayed German programs"
    )
    de_names = {item.canonical_name for item in de_programs}
    leaked = {
        item.canonical_name
        for item in fixture.programs
        if (item.country_code or "").upper() != "DE" and item.canonical_name not in de_names
    }
    assert not (leaked & listed), "non-DE fixture programs must not leak into ?country=DE"


async def test_second_post_returns_the_same_in_flight_run(
    demo_env: DemoEnv, api: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(demo_runner, "STEP_DELAY_SECONDS", 0.05)

    first, second = await asyncio.gather(
        api.post("/api/v1/research/demo", headers=demo_env.headers),
        api.post("/api/v1/research/demo", headers=demo_env.headers),
    )
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    first_id = first.json()["research_plan_id"]
    second_id = second.json()["research_plan_id"]
    assert first_id == second_id, "concurrent demo posts must share one run"

    await _wait_for_terminal(api, demo_env.headers, uuid.UUID(first_id))

    demo_env.session.expire_all()
    profile = (
        await demo_env.session.execute(
            select(StudentProfile).where(StudentProfile.user_id == demo_env.user_id)
        )
    ).scalars().one()
    plans = (
        await demo_env.session.execute(
            select(ResearchPlan).where(
                ResearchPlan.profile_id == profile.id, ResearchPlan.mode == "demo"
            )
        )
    ).scalars().all()
    assert len(plans) == 1, "a profile must never accumulate demo runs"
    assert plans[0].status.value == "SUCCEEDED"


async def test_cancel_mid_run_stops_remaining_steps(
    demo_env: DemoEnv, api: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(demo_runner, "STEP_DELAY_SECONDS", 0.1)

    response = await api.post("/api/v1/research/demo", headers=demo_env.headers)
    assert response.status_code == 200, response.text
    plan_id = uuid.UUID(response.json()["research_plan_id"])

    # Wait until at least one step finished so the run is genuinely in flight.
    deadline = time.monotonic() + 10
    while True:
        steps = await _steps(api, demo_env.headers, plan_id)
        if sum(1 for s in steps if s["status"] == "SUCCEEDED") >= 1:
            break
        assert time.monotonic() < deadline, "demo run never started"
        await asyncio.sleep(0.02)

    cancel = await api.post(f"/api/v1/research/runs/{plan_id}/cancel", headers=demo_env.headers)
    assert cancel.status_code == 200, cancel.text
    assert cancel.json()["status"] == "CANCELLED"

    # The runner notices CANCELLED between steps and stops there.
    await asyncio.sleep(0.5)
    detail = (await api.get(f"/api/v1/research/runs/{plan_id}", headers=demo_env.headers)).json()
    assert detail["status"] == "CANCELLED"
    steps = await _steps(api, demo_env.headers, plan_id)
    statuses = [s["status"] for s in steps]
    assert "QUEUED" in statuses, "cancel must stop the run before every step executes"
    assert "RUNNING" not in statuses, "no step may be left stuck in RUNNING"
    await asyncio.sleep(0.15)  # let the background task fully exit before teardown


async def test_fixture_upsert_is_idempotent(demo_env: DemoEnv) -> None:
    from app.services.demo.fixture import apply_fixture

    fixture = load_fixture()
    session = demo_env.session

    first = await apply_fixture(session, fixture)
    await session.commit()
    rows_after_first = {
        "institutions": (await session.execute(select(Institution))).scalars().all(),
        "programs": (await session.execute(select(Program))).scalars().all(),
        "sources": (await session.execute(select(Source))).scalars().all(),
        "evidence": (await session.execute(select(Evidence))).scalars().all(),
        "requirements": (await session.execute(select(Requirement))).scalars().all(),
    }

    second = await apply_fixture(session, fixture)
    await session.commit()
    rows_after_second = {
        "institutions": (await session.execute(select(Institution))).scalars().all(),
        "programs": (await session.execute(select(Program))).scalars().all(),
        "sources": (await session.execute(select(Source))).scalars().all(),
        "evidence": (await session.execute(select(Evidence))).scalars().all(),
        "requirements": (await session.execute(select(Requirement))).scalars().all(),
    }

    assert first["created"]["programs"] > 0
    assert second["created"] == {
        "institutions": 0,
        "programs": 0,
        "sources": 0,
        "evidence": 0,
        "requirements": 0,
        "intakes": 0,
    }, "second apply must not create anything"
    assert {k: len(v) for k, v in rows_after_first.items()} == {
        k: len(v) for k, v in rows_after_second.items()
    }, "row counts must not change on re-apply"
    fixture_evidence_ids = {uuid.UUID(item.id) for item in fixture.evidence}
    assert len(fixture_evidence_ids) == len(fixture.evidence)
    assert fixture_evidence_ids <= {row.id for row in rows_after_second["evidence"]}
    fixture_program_names = {item.normalized_name for item in fixture.programs}
    assert fixture_program_names <= {row.normalized_name for row in rows_after_second["programs"]}

    # Country filter columns (audit D-2): replayed rows carry the capture's
    # ISO codes and the FK reference row was seeded (`countries` ships empty,
    # so an unseeded DE would violate programs/institutions country FKs).
    country_codes = {row.code for row in (await session.execute(select(Country))).scalars()}
    expected_codes = {
        item.country_code.strip().upper()
        for item in (*fixture.institutions, *fixture.programs)
        if item.country_code
    }
    assert expected_codes <= country_codes, "fixture country codes must be reference rows"
    program_countries = {
        item.normalized_name: item.country_code for item in fixture.programs if item.country_code
    }
    for row in rows_after_second["programs"]:
        if row.normalized_name in program_countries:
            assert row.country_code == program_countries[row.normalized_name]


def test_demo_fixture_catalog_is_multi_country_and_officially_sourced() -> None:
    """W15: the demo catalog is a 10-14 program, >=3-country catalog in which
    every URL-carrying program is backed by an official university page with
    full evidence provenance (source_url, domain, retrieval + freshness)."""
    fixture = load_fixture()

    assert 10 <= len(fixture.programs) <= 14
    countries = {(item.country_code or "").strip().upper() for item in fixture.programs}
    assert "" not in countries, "every fixture program must carry a country code"
    assert len(countries) >= 3, f"catalog must span at least 3 countries, got {sorted(countries)}"
    for inst in fixture.institutions:
        assert (inst.country_code or "").strip(), (
            f"{inst.canonical_name}: every institution needs a country_code "
            "(countries FK seeding depends on it)"
        )

    official_urls = {
        src.canonical_url
        for src in fixture.sources
        if src.source_authority == "OFFICIAL_UNIVERSITY"
    }
    evidence_by_program: dict[str, list[Any]] = {}
    for ev in fixture.evidence:
        if ev.subject_ref is not None:
            evidence_by_program.setdefault(ev.subject_ref, []).append(ev)

    for program in fixture.programs:
        if program.official_url is None:
            # The two programs captured by the original run predate W15 and
            # carry no official_url; every W15 program must have one.
            continue
        assert program.official_url in official_urls, (
            f"{program.canonical_name}: official_url must be an OFFICIAL_UNIVERSITY source"
        )
        linked = evidence_by_program.get(program.id, [])
        assert linked, f"{program.canonical_name}: must have at least one evidence row"
        assert any(ev.source_url == program.official_url for ev in linked), (
            f"{program.canonical_name}: evidence must cite the official program page"
        )
        for ev in linked:
            assert ev.source_url and ev.source_domain, "evidence needs provenance"
            assert ev.retrieved_at is not None and ev.freshness_deadline is not None


async def test_persona_fills_only_empty_fields_for_participant(demo_env: DemoEnv) -> None:
    session = demo_env.session
    profile = StudentProfile(
        user_id=demo_env.user.id,
        institution_name="My Chosen Institute",  # user-provided: must survive
        career_goal="ML engineer at a climate startup",  # user-provided: must survive
    )
    session.add(profile)
    await session.flush()
    session.add(ProfilePreference(profile_id=profile.id, preferred_countries=["FR"]))
    await session.commit()

    assert await apply_demo_persona(session, profile) is True

    assert profile.institution_name == "My Chosen Institute"
    assert profile.career_goal == "ML engineer at a climate startup"
    assert profile.current_degree == "B.Tech Computer Science"
    assert profile.field_of_study == "Artificial Intelligence"
    assert profile.cgpa == Decimal("8.1")
    assert profile.cgpa_scale == Decimal("10")
    assert profile.total_budget_amount == Decimal("1800000")
    assert profile.total_experience_months == 12

    prefs = (
        await session.execute(
            select(ProfilePreference).where(ProfilePreference.profile_id == profile.id)
        )
    ).scalars().one()
    assert prefs.preferred_countries == ["FR"], "user preference must survive the persona"
    assert prefs.target_intakes == ["Winter 2027"]

    scores = (
        await session.execute(select(StudentTestScore).where(StudentTestScore.profile_id == profile.id))
    ).scalars().all()
    assert {s.test_type for s in scores} == {IELTS_TEST_TYPE, ENGLISH_TEST_TYPE}
    experiences = (
        await session.execute(select(Experience).where(Experience.profile_id == profile.id))
    ).scalars().all()
    assert len(experiences) == 1
    assert experiences[0].experience_type == "internship"
    start, end = experiences[0].start_date, experiences[0].end_date
    assert start is not None and end is not None
    # 1 Jul -> 30 Jun spans 12 calendar months inclusive.
    assert (end.year - start.year) * 12 + (end.month - start.month) + 1 == 12

    # Idempotent: a second application changes nothing.
    assert await apply_demo_persona(session, profile) is False
    scores_after = (
        await session.execute(select(StudentTestScore).where(StudentTestScore.profile_id == profile.id))
    ).scalars().all()
    assert len(scores_after) == len(scores)
