"""Typed response-shape tests for the API contract (TEST_PLAN §Contract).

Every response body asserted here is checked for its exact key set *and* the
JSON type of each field — including nullable branches (`top_risk | null`,
`next_deadline | null`). Where `api/API_CONTRACT.md` documents a shape, the
implementation is what gets asserted; any doc/implementation divergence is
reported rather than papered over (the doc is owned elsewhere).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.models import (
    ApplicationPlan,
    ConfidenceLevel,
    Country,
    Evidence,
    EvidenceStatus,
    FitAssessment,
    Institution,
    Notification,
    Program,
    ProgramCategory,
    Risk,
    RiskSeverity,
    RiskStatus,
    RunStatus,
    Source,
    SourceAuthority,
    StrategyRun,
    StudentProfile,
    User,
    UserRole,
)


def _email(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}@example.com"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _exact_keys(body: dict[str, Any], expected: set[str], where: str) -> None:
    assert set(body) == expected, f"{where}: got {sorted(body)}, want {sorted(expected)}"


def _is_int(value: Any) -> bool:
    # bool is an int subclass in Python; contract counters must be plain ints.
    return type(value) is int


async def _register(api: AsyncClient, email: str) -> dict[str, Any]:
    response = await api.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "correct-horse-1"},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _ensure_demo_profile(db_session: Any) -> None:
    """Seed the shared demo account so anonymous traffic (notifications,
    demo replay) resolves to the demo profile, never a registered user."""
    from app.services.profile import DEMO_EMAIL, get_or_create_profile

    existing = (
        await db_session.execute(select(User).where(User.email == DEMO_EMAIL))
    ).scalar_one_or_none()
    if existing is None:
        db_session.add(User(email=DEMO_EMAIL, full_name="Demo Student", role=UserRole.ADMIN))
    await get_or_create_profile(db_session)
    await db_session.commit()


async def _profile_for(db_session: Any, user_id: str) -> StudentProfile:
    existing = (
        (
            await db_session.execute(
                select(StudentProfile).where(StudentProfile.user_id == uuid.UUID(user_id))
            )
        )
        .scalars()
        .one_or_none()
    )
    if existing is not None:
        return existing
    profile = StudentProfile(user_id=uuid.UUID(user_id))
    db_session.add(profile)
    await db_session.flush()
    return profile


@pytest.fixture
async def env(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[dict[str, Any]]:
    """A registered owner with a fully-populated strategy card (program, fit,
    risk, evidence), an open evidence conflict, and one demo notification."""
    from app import main as main_module

    main_module._rate_counters.clear()
    await _ensure_demo_profile(db_session)

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as api:
        owner = await _register(api, _email("shape-owner"))
        owner_profile = await _profile_for(db_session, owner["user"]["id"])

        strategy = StrategyRun(
            profile_id=owner_profile.id,
            status=RunStatus.SUCCEEDED,
            scoring_version="v1",
            strategy_version="v1",
            summary="1 program scored - health: 0 blockers",
        )
        db_session.add(strategy)
        await db_session.flush()

        country = (
            (await db_session.execute(select(Country).where(Country.code == "NL"))).scalars().first()
        )
        if country is None:
            db_session.add(Country(code="NL", name="Netherlands"))
            await db_session.flush()
        institution = Institution(
            canonical_name="Shape University",
            normalized_name="shape university",
            domain="shape.example.edu",
        )
        db_session.add(institution)
        await db_session.flush()
        program = Program(
            institution_id=institution.id,
            canonical_name="M.Sc. Shape Testing",
            normalized_name="m.sc. shape testing",
            country_code="NL",
            tuition_amount=Decimal("20000"),
            tuition_currency="EUR",
        )
        db_session.add(program)
        await db_session.flush()

        fit = FitAssessment(
            profile_id=owner_profile.id,
            research_plan_id=None,
            program_id=program.id,
            academic_score=Decimal("80.00"),
            language_score=Decimal("60.00"),
            overall_score=Decimal("55.00"),
            scoring_version="v1",
            explanation="Fit score 55.00 - this is not an admission probability.",
        )
        db_session.add(fit)
        await db_session.flush()

        plan = ApplicationPlan(
            strategy_run_id=strategy.id,
            profile_id=owner_profile.id,
            program_id=program.id,
            category=ProgramCategory.TARGET,
            priority=1,
            fit_assessment_id=fit.id,
            rationale="Fit score 55.00",
            reasons=["Academic 80"],
            estimated_cost={"currency": "EUR", "amount": 20000.0, "band": "MEDIUM"},
            next_deadline=datetime.now(UTC) + timedelta(days=120),
            next_action="Prepare transcripts",
        )
        db_session.add(plan)
        risk = Risk(
            profile_id=owner_profile.id,
            program_id=program.id,
            risk_type="LANGUAGE",
            severity=RiskSeverity.HIGH,
            title="Requirement not met: language_ielts_overall",
            reason="Mandatory requirement not satisfied.",
            recommended_action="Address language_ielts_overall before applying.",
            status=RiskStatus.OPEN,
            confidence=ConfidenceLevel.MEDIUM,
        )
        db_session.add(risk)

        source = Source(
            url="https://shape.example.edu/ai",
            canonical_url=f"https://shape.example.edu/ai-{uuid.uuid4().hex[:8]}",
            domain="shape.example.edu",
            title="Shape University",
            source_authority=SourceAuthority.OFFICIAL_UNIVERSITY,
            last_seen_at=datetime.now(UTC),
        )
        db_session.add(source)
        await db_session.flush()
        now = datetime.now(UTC)
        evidence_a = Evidence(
            source_id=source.id,
            claim_type="deadline",
            subject_type="program",
            subject_id=program.id,
            claim="Applications close 1 March",
            normalized_claim="application_deadline",
            extracted_value={"date": "2027-03-01"},
            confidence=ConfidenceLevel.HIGH,
            status=EvidenceStatus.CURRENT,
            retrieved_at=now,
            freshness_deadline=now + timedelta(days=90),
        )
        evidence_b = Evidence(
            source_id=source.id,
            claim_type="deadline",
            subject_type="program",
            subject_id=program.id,
            claim="Applications close 15 January",
            normalized_claim="application_deadline",
            extracted_value={"date": "2027-01-15"},
            confidence=ConfidenceLevel.MEDIUM,
            status=EvidenceStatus.CURRENT,
            retrieved_at=now,
            freshness_deadline=now + timedelta(days=90),
        )
        db_session.add_all([evidence_a, evidence_b])
        await db_session.flush()

        # An open conflict with both claims as members (resolve-shape test).
        from app.db.models import EvidenceConflict, EvidenceConflictMember

        conflict = EvidenceConflict(
            conflict_key=f"program:{program.id}:application_deadline",
            description="Deadline differs across sources",
            resolution_status="UNRESOLVED",
        )
        db_session.add(conflict)
        await db_session.flush()
        db_session.add_all(
            [
                EvidenceConflictMember(conflict_id=conflict.id, evidence_id=evidence_a.id),
                EvidenceConflictMember(conflict_id=conflict.id, evidence_id=evidence_b.id),
            ]
        )

        # One notification for the *demo* account (anonymous inbox).
        from app.services.profile import get_or_create_default_user

        demo_user = await get_or_create_default_user(db_session)
        db_session.add(
            Notification(
                user_id=demo_user.id,
                type="RESEARCH_COMPLETE",
                title="Research run finished",
                body="Your research run finished with 3 results.",
                link="/research",
                payload={},
                email_status="SKIPPED",
            )
        )
        await db_session.commit()

        yield {
            "api": api,
            "db_session": db_session,
            "owner_token": owner["token"],
            "strategy_id": strategy.id,
            "program_id": program.id,
            "risk_id": risk.id,
            "conflict_id": conflict.id,
            "profile_id": owner_profile.id,
        }


# ------------------------------------------------------------------- auth


async def test_register_login_and_me_response_shapes(env: dict[str, Any]) -> None:
    api: AsyncClient = env["api"]
    body = await _register(api, _email("shape-auth"))
    _exact_keys(body, {"token", "user"}, "POST /auth/register")
    assert isinstance(body["token"], str) and body["token"]
    user = body["user"]
    _exact_keys(user, {"id", "email", "full_name", "role"}, "register.user")
    uuid.UUID(user["id"])  # UUID identifiers (API_CONTRACT conventions)
    assert isinstance(user["email"], str) and user["email"]
    assert user["full_name"] is None or isinstance(user["full_name"], str)
    assert isinstance(user["role"], str) and user["role"]

    login = await api.post(
        "/api/v1/auth/login",
        json={"email": body["user"]["email"], "password": "correct-horse-1"},
    )
    assert login.status_code == 200, login.text
    login_body = login.json()
    _exact_keys(login_body, {"token", "user"}, "POST /auth/login")
    assert isinstance(login_body["token"], str) and login_body["token"]

    me = await api.get("/api/v1/auth/me", headers=_auth(body["token"]))
    assert me.status_code == 200, me.text
    me_body = me.json()
    _exact_keys(me_body, {"user"}, "GET /auth/me")
    _exact_keys(me_body["user"], {"id", "email", "full_name", "role"}, "GET /auth/me.user")
    assert me_body["user"]["id"] == user["id"]


# --------------------------------------------------------- notifications


async def test_notifications_list_response_shape(env: dict[str, Any]) -> None:
    api: AsyncClient = env["api"]
    response = await api.get("/api/v1/notifications")
    assert response.status_code == 200, response.text
    body = response.json()
    _exact_keys(body, {"items", "unread_count"}, "GET /notifications")
    assert _is_int(body["unread_count"]) and body["unread_count"] >= 1
    assert isinstance(body["items"], list) and body["items"], "seeded notification must show"
    item = body["items"][0]
    _exact_keys(
        item,
        {"id", "type", "title", "body", "link", "read", "email_status", "created_at"},
        "notifications.items[0]",
    )
    uuid.UUID(item["id"])
    assert isinstance(item["type"], str)
    assert isinstance(item["title"], str)
    assert isinstance(item["body"], str)
    assert item["link"] is None or isinstance(item["link"], str)
    assert isinstance(item["read"], bool) and item["read"] is False
    assert isinstance(item["email_status"], str)
    assert isinstance(item["created_at"], str)  # ISO-8601 timestamp


# ----------------------------------------------------------- strategies


async def test_strategies_list_response_shape(env: dict[str, Any]) -> None:
    api: AsyncClient = env["api"]
    response = await api.get("/api/v1/strategies", headers=_auth(env["owner_token"]))
    assert response.status_code == 200, response.text
    body = response.json()
    _exact_keys(body, {"items"}, "GET /strategies")
    assert isinstance(body["items"], list) and body["items"]
    item = body["items"][0]
    _exact_keys(
        item,
        {"id", "status", "plan_health_score", "summary", "created_at"},
        "strategies.items[0]",
    )
    uuid.UUID(item["id"])
    assert isinstance(item["status"], str)
    assert item["plan_health_score"] is None or isinstance(item["plan_health_score"], str)
    assert item["summary"] is None or isinstance(item["summary"], str)
    assert isinstance(item["created_at"], str)


# ------------------------------------------------------ portfolio cards


async def test_portfolio_row_response_shape(env: dict[str, Any]) -> None:
    """Typed portfolio card: every documented field present with its JSON type
    (fit_score string-or-null, top_risk object, freshness counters ints)."""
    api: AsyncClient = env["api"]
    response = await api.get(
        f"/api/v1/strategies/{env['strategy_id']}/portfolio", headers=_auth(env["owner_token"])
    )
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert isinstance(items, list) and items
    card = items[0]
    _exact_keys(
        card,
        {
            "program_id",
            "program_name",
            "institution",
            "category",
            "priority",
            "rationale",
            "fit_score",
            "reasons",
            "top_risk",
            "estimated_cost",
            "next_deadline",
            "next_action",
            "evidence_freshness",
        },
        "portfolio.items[0]",
    )
    uuid.UUID(card["program_id"])
    assert card["program_name"] is None or isinstance(card["program_name"], str)
    assert card["institution"] is None or isinstance(card["institution"], str)
    assert isinstance(card["category"], str)
    assert _is_int(card["priority"])
    assert card["rationale"] is None or isinstance(card["rationale"], str)
    # Scores are strings-or-null, never invented numbers.
    assert card["fit_score"] is None or isinstance(card["fit_score"], str)
    assert isinstance(card["reasons"], list)
    assert all(isinstance(r, str) for r in card["reasons"])
    risk = card["top_risk"]
    assert risk is not None and isinstance(risk, dict)
    _exact_keys(risk, {"id", "severity", "risk_type", "title"}, "portfolio.top_risk")
    uuid.UUID(risk["id"])
    assert isinstance(risk["severity"], str)
    assert isinstance(risk["risk_type"], str)
    assert isinstance(risk["title"], str)
    assert isinstance(card["estimated_cost"], dict)
    assert all(isinstance(k, str) for k in card["estimated_cost"])
    assert card["next_deadline"] is None or isinstance(card["next_deadline"], str)
    assert card["next_action"] is None or isinstance(card["next_action"], str)
    freshness = card["evidence_freshness"]
    _exact_keys(freshness, {"status", "stale", "total"}, "portfolio.evidence_freshness")
    assert isinstance(freshness["status"], str)
    assert _is_int(freshness["stale"]) and _is_int(freshness["total"])


async def test_portfolio_row_top_risk_is_null_when_no_risk_exists(env: dict[str, Any]) -> None:
    """The `top_risk | null` branch: a portfolio row without an OPEN risk must
    serialize as JSON null, not an empty object."""
    api: AsyncClient = env["api"]
    db = env["db_session"]
    strategy = StrategyRun(
        profile_id=env["profile_id"],
        status=RunStatus.SUCCEEDED,
        scoring_version="v1",
        strategy_version="v1",
        summary="no risks",
    )
    db.add(strategy)
    await db.flush()
    institution = Institution(
        canonical_name="Riskless University", normalized_name="riskless university"
    )
    db.add(institution)
    await db.flush()
    program = Program(
        institution_id=institution.id,
        canonical_name="M.Sc. Riskless",
        normalized_name="m.sc. riskless",
    )
    db.add(program)
    await db.flush()
    db.add(
        ApplicationPlan(
            strategy_run_id=strategy.id,
            profile_id=strategy.profile_id,
            program_id=program.id,
            category=ProgramCategory.REACH,
            priority=2,
            rationale="no data",
        )
    )
    await db.commit()

    response = await api.get(
        f"/api/v1/strategies/{strategy.id}/portfolio", headers=_auth(env["owner_token"])
    )
    assert response.status_code == 200, response.text
    card = response.json()["items"][0]
    assert card["top_risk"] is None
    assert card["fit_score"] is None
    assert card["next_deadline"] is None
    assert card["evidence_freshness"] == {"status": "UNKNOWN", "stale": 0, "total": 0}


# ------------------------------------------------------ evidence health


async def test_evidence_health_response_shape(env: dict[str, Any]) -> None:
    api: AsyncClient = env["api"]
    response = await api.get(
        f"/api/v1/strategies/{env['strategy_id']}/evidence-health",
        headers=_auth(env["owner_token"]),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    _exact_keys(
        body,
        {
            "programs_total",
            "programs_with_evidence",
            "evidence_total",
            "by_status",
            "by_confidence",
            "by_authority",
            "stale_count",
            "unknowns",
            "conflicting_count",
        },
        "GET /evidence-health",
    )
    for counter in (
        "programs_total",
        "programs_with_evidence",
        "evidence_total",
        "stale_count",
        "unknowns",
        "conflicting_count",
    ):
        assert _is_int(body[counter]) and body[counter] >= 0, counter
    for grouping in ("by_status", "by_confidence", "by_authority"):
        assert isinstance(body[grouping], dict), grouping
        assert all(
            isinstance(k, str) and _is_int(v) for k, v in body[grouping].items()
        ), grouping


# ---------------------------------------------------------- risk patch


async def test_patch_risk_response_shape(env: dict[str, Any]) -> None:
    api: AsyncClient = env["api"]
    url = f"/api/v1/strategies/{env['strategy_id']}/risks/{env['risk_id']}"
    response = await api.patch(url, json={"status": "ACKNOWLEDGED"}, headers=_auth(env["owner_token"]))
    assert response.status_code == 200, response.text
    body = response.json()
    _exact_keys(body, {"risk"}, "PATCH /risks/{id}")
    risk = body["risk"]
    _exact_keys(
        risk,
        {
            "id",
            "risk_type",
            "severity",
            "title",
            "reason",
            "recommended_action",
            "status",
            "confidence",
            "program_id",
            "requirement_id",
            "resolved_at",
        },
        "PATCH /risks/{id}.risk",
    )
    uuid.UUID(risk["id"])
    assert isinstance(risk["risk_type"], str)
    assert isinstance(risk["severity"], str)
    assert isinstance(risk["title"], str)
    assert isinstance(risk["reason"], str)
    assert isinstance(risk["recommended_action"], str)
    assert risk["status"] == "ACKNOWLEDGED"
    assert isinstance(risk["confidence"], str)
    assert risk["program_id"] is None or isinstance(risk["program_id"], str)
    assert risk["requirement_id"] is None or isinstance(risk["requirement_id"], str)
    assert risk["resolved_at"] is None or isinstance(risk["resolved_at"], str)


# ------------------------------------------------------ conflict resolve


async def test_conflict_resolve_response_shape(env: dict[str, Any]) -> None:
    api: AsyncClient = env["api"]
    response = await api.post(
        f"/api/v1/evidence/conflicts/{env['conflict_id']}/resolve",
        json={"reason": "Official source wins"},
        # P1-10: conflicts attached to a profile are 404 for anyone but the owner.
        headers=_auth(env["owner_token"]),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    _exact_keys(
        body,
        {
            "id",
            "conflict_key",
            "resolution_status",
            "preferred_evidence_id",
            "resolution_reason",
            "resolved_at",
        },
        "POST /evidence/conflicts/{id}/resolve",
    )
    uuid.UUID(body["id"])
    assert body["id"] == str(env["conflict_id"])
    assert isinstance(body["conflict_key"], str)
    assert body["resolution_status"] == "RESOLVED"
    if body["preferred_evidence_id"] is not None:
        uuid.UUID(body["preferred_evidence_id"])
    assert body["resolution_reason"] == "Official source wins"
    assert isinstance(body["resolved_at"], str)  # ISO-8601


# ---------------------------------------------------------- demo replay


async def test_demo_replay_response_shape(env: dict[str, Any]) -> None:
    api: AsyncClient = env["api"]
    response = await api.post("/api/v1/research/demo")
    assert response.status_code == 200, response.text
    body = response.json()
    _exact_keys(body, {"research_plan_id", "status"}, "POST /research/demo")
    uuid.UUID(body["research_plan_id"])
    assert body["status"] in {status.value for status in RunStatus}
