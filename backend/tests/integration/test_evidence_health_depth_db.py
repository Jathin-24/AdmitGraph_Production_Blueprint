"""P2-17: evidence-health depth and roadmap evidence links.

The frontend renders "why this matters" links from two places this asserts
end-to-end, against real PostgreSQL (fixture: tests/conftest.py):

* `GET /strategies/{id}/evidence-health` — the *depth* of the counters W5
  consumes (`by_authority`, `stale_count`, `unknowns`, `conflicting_count`),
  not merely their presence, and scoped to the portfolio's programs;
* `GET /strategies/{id}` / `/roadmap` — `roadmap_tasks[].evidence_ids` is a
  list of evidence UUID strings the UI can turn into links.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.models import (
    ApplicationPlan,
    ConfidenceLevel,
    Evidence,
    EvidenceStatus,
    Institution,
    Program,
    ProgramCategory,
    RoadmapTask,
    RunStatus,
    Source,
    SourceAuthority,
    StrategyRun,
    StudentProfile,
    TaskStatus,
)

_STRATEGY_KEYS = {
    "id",
    "status",
    "plan_health_score",
    "summary",
    "scoring_version",
    "strategy_version",
    "created_at",
    "portfolio",
    "risks",
    "roadmap_tasks",
}
_TASK_KEYS = {"id", "title", "task_type", "status", "due_date", "evidence_ids"}
_HEALTH_KEYS = {
    "programs_total",
    "programs_with_evidence",
    "evidence_total",
    "by_status",
    "by_confidence",
    "by_authority",
    "stale_count",
    "unknowns",
    "conflicting_count",
}


@pytest.fixture
async def api(db_session: Any) -> AsyncIterator[AsyncClient]:
    from app import main as main_module
    from app.main import app

    main_module._rate_counters.clear()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


async def _register(api: AsyncClient) -> tuple[str, str]:
    """(token, user id) for a fresh student."""
    email = f"health-{uuid.uuid4().hex[:8]}@example.com"
    response = await api.post(
        "/api/v1/auth/register", json={"email": email, "password": "correct-horse-1"}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return str(body["token"]), str(body["user"]["id"])


async def _seed_program(session: Any, tag: str) -> Program:
    institution = Institution(
        canonical_name=f"Health University {tag}",
        normalized_name=f"health university {tag}",
        domain=f"health-{tag}.example.test",
    )
    session.add(institution)
    await session.flush()
    program = Program(
        institution_id=institution.id,
        canonical_name=f"M.Sc. Health Testing {tag}",
        normalized_name=f"m.sc. health testing {tag}",
    )
    session.add(program)
    await session.flush()
    return program


async def _seed_evidence(
    session: Any,
    *,
    subject_id: uuid.UUID,
    domain: str,
    authority: SourceAuthority,
    status: EvidenceStatus,
    confidence: ConfidenceLevel,
    stale: bool = False,
    claim: str = "Page states a deadline.",
) -> Evidence:
    now = datetime.now(UTC)
    source = Source(
        url=f"https://{domain}/page",
        canonical_url=f"https://{domain}/page-{uuid.uuid4().hex[:6]}",
        domain=domain,
        title=f"Source {domain}",
        source_authority=authority,
        last_seen_at=now,
    )
    session.add(source)
    await session.flush()
    evidence = Evidence(
        source_id=source.id,
        claim_type="deadline",
        subject_type="program",
        subject_id=subject_id,
        claim=claim,
        normalized_claim="application_deadline",
        extracted_value={"date": "2027-03-01"},
        confidence=confidence,
        status=status,
        retrieved_at=now,
        freshness_deadline=now - timedelta(days=1) if stale else now + timedelta(days=90),
    )
    session.add(evidence)
    await session.flush()
    return evidence


@pytest.fixture
async def env(db_session: Any, api: AsyncClient) -> AsyncIterator[dict[str, Any]]:
    """One strategy whose portfolio holds four pieces of evidence (one per
    dimension asserted below) plus an off-portfolio decoy program."""
    token, user_id = await _register(api)
    headers = {"Authorization": f"Bearer {token}"}
    profile = (
        (
            await db_session.execute(
                select(StudentProfile).where(StudentProfile.user_id == uuid.UUID(user_id))
            )
        )
        .scalars()
        .one()
    )
    assert profile.id is not None

    strategy = StrategyRun(
        profile_id=profile.id,
        status=RunStatus.SUCCEEDED,
        scoring_version="v1",
        strategy_version="v1",
        summary="1 program scored - health: 0 blockers",
    )
    db_session.add(strategy)
    await db_session.flush()

    program = await _seed_program(db_session, uuid.uuid4().hex[:6])
    db_session.add(
        ApplicationPlan(
            strategy_run_id=strategy.id,
            profile_id=profile.id,
            program_id=program.id,
            category=ProgramCategory.TARGET,
            priority=1,
            rationale="Fit score unknown",
            next_action="Prepare transcripts",
        )
    )

    evidence_official = await _seed_evidence(
        db_session,
        subject_id=program.id,
        domain=f"official-{uuid.uuid4().hex[:6]}.example.test",
        authority=SourceAuthority.OFFICIAL_UNIVERSITY,
        status=EvidenceStatus.CURRENT,
        confidence=ConfidenceLevel.HIGH,
        claim="Official page: applications close 1 March.",
    )
    evidence_forum = await _seed_evidence(
        db_session,
        subject_id=program.id,
        domain=f"forum-{uuid.uuid4().hex[:6]}.example.test",
        authority=SourceAuthority.FORUM_SOCIAL,
        status=EvidenceStatus.CURRENT,
        confidence=ConfidenceLevel.MEDIUM,
        stale=True,
        claim="Forum thread repeats the deadline.",
    )
    evidence_unknown = await _seed_evidence(
        db_session,
        subject_id=program.id,
        domain=f"unknown-{uuid.uuid4().hex[:6]}.example.test",
        authority=SourceAuthority.UNKNOWN,
        status=EvidenceStatus.UNAVAILABLE,
        confidence=ConfidenceLevel.LOW,
        claim="Fetch failed: no page body.",
    )
    evidence_conflicting = await _seed_evidence(
        db_session,
        subject_id=program.id,
        domain=f"conflict-{uuid.uuid4().hex[:6]}.example.test",
        authority=SourceAuthority.CREDIBLE_SECONDARY,
        status=EvidenceStatus.CONFLICTING,
        confidence=ConfidenceLevel.LOW,
        claim="Another source reports a different deadline.",
    )

    # Decoy: same shape, but NOT in this strategy's portfolio — it must never
    # inflate the health counters.
    decoy_program = await _seed_program(db_session, uuid.uuid4().hex[:6])
    await _seed_evidence(
        db_session,
        subject_id=decoy_program.id,
        domain=f"decoy-{uuid.uuid4().hex[:6]}.example.test",
        authority=SourceAuthority.OFFICIAL_UNIVERSITY,
        status=EvidenceStatus.CURRENT,
        confidence=ConfidenceLevel.HIGH,
        claim="Decoy page: applications close 1 April.",
    )

    linked_ids = [str(evidence_official.id), str(evidence_conflicting.id)]
    task = RoadmapTask(
        strategy_run_id=strategy.id,
        profile_id=profile.id,
        program_id=program.id,
        title="Prepare transcripts",
        description="Upload certified transcripts.",
        task_type="DOCUMENT",
        due_date=date.today() + timedelta(days=30),
        status=TaskStatus.TODO,
        evidence_ids=linked_ids,
    )
    bare_task = RoadmapTask(
        strategy_run_id=strategy.id,
        profile_id=profile.id,
        program_id=program.id,
        title="Book the language test",
        description=None,
        task_type="LANGUAGE",
        due_date=date.today() + timedelta(days=60),
        status=TaskStatus.TODO,
        evidence_ids=[],
    )
    db_session.add_all([task, bare_task])
    await db_session.commit()

    yield {
        "api": api,
        "headers": headers,
        "strategy_id": strategy.id,
        "program_id": program.id,
        "profile_id": profile.id,
        "linked_ids": linked_ids,
        "task_ids": [str(task.id), str(bare_task.id)],
        "user_id": user_id,
        "evidence_ids": [
            str(evidence_official.id),
            str(evidence_forum.id),
            str(evidence_unknown.id),
            str(evidence_conflicting.id),
        ],
    }


async def test_evidence_health_reports_every_breakdown_depth(env: dict[str, Any]) -> None:
    """Counters are real breakdowns of the portfolio's evidence — authority,
    freshness, unavailable and conflicting — never placeholders."""
    api: AsyncClient = env["api"]
    response = await api.get(
        f"/api/v1/strategies/{env['strategy_id']}/evidence-health", headers=env["headers"]
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == _HEALTH_KEYS

    assert body["programs_total"] == 1, "only the portfolio's programs count"
    assert body["programs_with_evidence"] == 1
    assert body["evidence_total"] == 4, "the off-portfolio decoy must not inflate totals"

    assert body["by_status"] == {
        "CURRENT": 2,
        "UNAVAILABLE": 1,
        "CONFLICTING": 1,
    }
    assert body["by_authority"] == {
        "OFFICIAL_UNIVERSITY": 1,
        "FORUM_SOCIAL": 1,
        "UNKNOWN": 1,
        "CREDIBLE_SECONDARY": 1,
    }
    assert set(body["by_confidence"]) == {"HIGH", "MEDIUM", "LOW"}
    assert sum(body["by_confidence"].values()) == 4

    assert body["stale_count"] == 1, "exactly one claim is past its freshness deadline"
    assert body["unknowns"] == 1, "one UNAVAILABLE claim is an unknown"
    assert body["conflicting_count"] == 1

    for counter in ("stale_count", "unknowns", "conflicting_count"):
        assert type(body[counter]) is int and body[counter] >= 0, counter


async def test_strategy_detail_roadmap_tasks_carry_evidence_links(
    env: dict[str, Any],
) -> None:
    """`roadmap_tasks[].evidence_ids` is a list of evidence UUID strings the UI
    can render as "why" links (empty list when a task has no evidence)."""
    api: AsyncClient = env["api"]
    response = await api.get(f"/api/v1/strategies/{env['strategy_id']}", headers=env["headers"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == _STRATEGY_KEYS

    tasks = body["roadmap_tasks"]
    assert isinstance(tasks, list) and len(tasks) == 2
    by_id = {t["id"]: t for t in tasks}
    assert set(by_id) == set(env["task_ids"])

    for task in tasks:
        assert set(task) == _TASK_KEYS, task
        uuid.UUID(task["id"])
        assert isinstance(task["title"], str)
        assert isinstance(task["task_type"], str)
        assert task["status"] in {s.value for s in TaskStatus}
        assert task["due_date"] is None or isinstance(task["due_date"], str)
        assert isinstance(task["evidence_ids"], list)
        for evidence_id in task["evidence_ids"]:
            assert isinstance(evidence_id, str)
            uuid.UUID(evidence_id)  # a link target, not free text

    linked = by_id[env["task_ids"][0]]
    assert linked["evidence_ids"] == env["linked_ids"], (
        "the seeded evidence must round-trip unchanged"
    )
    assert by_id[env["task_ids"][1]]["evidence_ids"] == [], "no evidence stays an empty list"


async def test_roadmap_endpoint_returns_the_same_evidence_links(env: dict[str, Any]) -> None:
    """`/roadmap` is the same task list (and evidence links) as the detail."""
    api: AsyncClient = env["api"]
    response = await api.get(
        f"/api/v1/strategies/{env['strategy_id']}/roadmap", headers=env["headers"]
    )
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [t["id"] for t in items] == env["task_ids"]
    assert items[0]["evidence_ids"] == env["linked_ids"]
    assert items[1]["evidence_ids"] == []


async def test_evidence_health_is_inaccessible_to_another_user(env: dict[str, Any]) -> None:
    """P1-10: a strategy id belonging to someone else is a 404, not a leak."""
    api: AsyncClient = env["api"]
    other_token, _ = await _register(api)
    response = await api.get(
        f"/api/v1/strategies/{env['strategy_id']}/evidence-health",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_seeded_evidence_still_belong_to_their_subject(env: dict[str, Any]) -> None:
    """Guard the fixture itself: every row this file counts really is attached
    to the portfolio program (so a future refactor can't silently re-scope)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.db.session import get_engine

    maker = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with maker() as session:
        rows = (
            (
                await session.execute(
                    select(Evidence).where(
                        Evidence.id.in_([uuid.UUID(i) for i in env["evidence_ids"]])
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 4
    assert {str(r.subject_id) for r in rows} == {str(env["program_id"])}
