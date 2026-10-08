"""Strategy API: caller scoping, portfolio card fields, evidence health, PATCH.

Covers MASTER_SPEC §17/§19 and TEST_PLAN §Security ("authorization prevents
cross-user profile access"): another user's strategy/risk id must be
indistinguishable from a missing one, the card must quote only stored values,
and risk transitions must be validated and owned.
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


async def _register(api: AsyncClient, email: str) -> dict[str, Any]:
    response = await api.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "correct-horse-1"},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _ensure_demo_profile(db_session: Any) -> None:
    """Seed the shared demo account so anonymous traffic never claims a
    registered user's profile (services/profile.py demo-account contract)."""
    from app.services.profile import DEMO_EMAIL, get_or_create_profile

    existing = (
        await db_session.execute(select(User).where(User.email == DEMO_EMAIL))
    ).scalar_one_or_none()
    if existing is None:
        db_session.add(User(email=DEMO_EMAIL, full_name="Demo Student", role=UserRole.ADMIN))
    await get_or_create_profile(db_session)
    await db_session.commit()


@pytest.fixture
async def env(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[dict[str, Any]]:
    """Two registered users and one strategy owned by `owner` (with a full
    portfolio card: program, fit, risk, evidence)."""
    from app import main as main_module

    main_module._rate_counters.clear()
    await _ensure_demo_profile(db_session)

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as api:
        owner = await _register(api, _email("strat-owner"))
        other = await _register(api, _email("strat-other"))

        owner_profile = await _profile_for(db_session, owner["user"]["id"])
        strategy = StrategyRun(
            profile_id=owner_profile.id,
            status=RunStatus.SUCCEEDED,
            scoring_version="v1",
            strategy_version="v1",
            summary="2 programs scored - health: 0 blockers",
        )
        db_session.add(strategy)
        await db_session.flush()

        institution = Institution(
            canonical_name="API Strategy University",
            normalized_name="api strategy university",
            domain="api-strategy.example.edu",
        )
        db_session.add(institution)
        await db_session.flush()
        # programs.country_code is a real FK: seed the country, never guess it.
        existing_country = (
            (await db_session.execute(select(Country).where(Country.code == "NL")))
            .scalars()
            .first()
        )
        if existing_country is None:
            db_session.add(Country(code="NL", name="Netherlands"))
            await db_session.flush()
        program = Program(
            institution_id=institution.id,
            canonical_name="M.Sc. API Strategy",
            normalized_name="m.sc. api strategy",
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
            explanation=(
                "Weighted from your profile - Academic 80. "
                "This is a fit score, not an admission probability."
            ),
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
            reasons=[],
            # What step_build_strategy stores for this program's tuition band.
            estimated_cost={"currency": "EUR", "amount": 20000.0, "band": "MEDIUM"},
            next_deadline=None,
            next_action=None,
        )
        db_session.add(plan)

        risk = Risk(
            profile_id=owner_profile.id,
            program_id=program.id,
            risk_type="LANGUAGE",
            severity=RiskSeverity.HIGH,
            title="Requirement not met: language_ielts_overall",
            reason="Mandatory requirement 'language_ielts_overall' is not satisfied by the current profile.",
            recommended_action="Address 'language_ielts_overall' before applying.",
            status=RiskStatus.OPEN,
            confidence=ConfidenceLevel.MEDIUM,
        )
        db_session.add(risk)

        source = Source(
            url="https://api-strategy.example.edu/ai",
            canonical_url=f"https://api-strategy.example.edu/ai-{uuid.uuid4().hex[:8]}",
            domain="api-strategy.example.edu",
            title="API Strategy University",
            source_authority=SourceAuthority.OFFICIAL_UNIVERSITY,
            last_seen_at=datetime.now(UTC),
        )
        db_session.add(source)
        await db_session.flush()
        now = datetime.now(UTC)
        db_session.add(
            Evidence(
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
        )
        db_session.add(
            Evidence(
                source_id=source.id,
                claim_type="tuition",
                subject_type="program",
                subject_id=program.id,
                claim="Tuition unknown",
                confidence=ConfidenceLevel.LOW,
                status=EvidenceStatus.UNAVAILABLE,
                retrieved_at=now,
            )
        )
        await db_session.commit()

        yield {
            "api": api,
            "db_session": db_session,
            "owner_token": owner["token"],
            "other_token": other["token"],
            "strategy_id": strategy.id,
            "program_id": program.id,
            "risk_id": risk.id,
            "profile_id": owner_profile.id,
        }


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


# ------------------------------------------------------------------ scoping


async def test_strategy_list_is_scoped_to_the_caller(env: dict[str, Any]) -> None:
    api: AsyncClient = env["api"]
    owned = await api.get("/api/v1/strategies", headers=_auth(env["owner_token"]))
    assert owned.status_code == 200, owned.text
    ids = [row["id"] for row in owned.json()["items"]]
    assert str(env["strategy_id"]) in ids

    stranger = await api.get("/api/v1/strategies", headers=_auth(env["other_token"]))
    assert stranger.status_code == 200, stranger.text
    assert str(env["strategy_id"]) not in [r["id"] for r in stranger.json()["items"]]


async def test_other_users_strategy_is_a_404_not_a_403(env: dict[str, Any]) -> None:
    api: AsyncClient = env["api"]
    strategy_id = env["strategy_id"]
    for path in (
        f"/api/v1/strategies/{strategy_id}",
        f"/api/v1/strategies/{strategy_id}/portfolio",
        f"/api/v1/strategies/{strategy_id}/risks",
        f"/api/v1/strategies/{strategy_id}/roadmap",
        f"/api/v1/strategies/{strategy_id}/evidence-health",
    ):
        response = await api.get(path, headers=_auth(env["other_token"]))
        assert response.status_code == 404, f"{path} -> {response.status_code}"
        body = response.json()["error"]
        assert body["code"] == "NOT_FOUND"
        assert body["request_id"]

    # The owner still sees it.
    owned = await api.get(f"/api/v1/strategies/{strategy_id}", headers=_auth(env["owner_token"]))
    assert owned.status_code == 200, owned.text


async def test_unknown_strategy_is_404_for_its_caller(env: dict[str, Any]) -> None:
    api: AsyncClient = env["api"]
    response = await api.get(
        "/api/v1/strategies/00000000-0000-0000-0000-000000000000",
        headers=_auth(env["owner_token"]),
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


# ------------------------------------------------------------ portfolio card


async def test_portfolio_card_carries_the_documented_fields(env: dict[str, Any]) -> None:
    api: AsyncClient = env["api"]
    response = await api.get(
        f"/api/v1/strategies/{env['strategy_id']}/portfolio", headers=_auth(env["owner_token"])
    )
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(items) == 1
    card = items[0]

    assert card["program_name"] == "M.Sc. API Strategy"
    assert card["institution"] == "API Strategy University"
    assert card["category"] == "TARGET"
    assert card["priority"] == 1
    assert card["fit_score"] == "55.00"
    # Top-2 reasons come from stored subscores (the plan's own list was empty,
    # so the linked fit's columns supply them).
    assert card["reasons"] and len(card["reasons"]) == 2
    assert card["reasons"][0].startswith("Academic 80")
    # The worst OPEN risk for this program.
    assert card["top_risk"] is not None
    assert card["top_risk"]["severity"] == "HIGH"
    assert card["top_risk"]["risk_type"] == "LANGUAGE"
    assert card["top_risk"]["title"].startswith("Requirement not met")
    # Cost band stored by the strategy build (never invented by the API).
    assert card["estimated_cost"] == {"currency": "EUR", "amount": 20000.0, "band": "MEDIUM"}
    assert card["next_deadline"] is None
    assert card["next_action"] is None
    # Evidence freshness: 2 claims, one CURRENT (fresh) + one UNAVAILABLE.
    assert card["evidence_freshness"] == {"status": "FRESH", "stale": 0, "total": 2}


async def test_portfolio_row_without_facts_stays_unknown(env: dict[str, Any]) -> None:
    """Missing data is reported as missing: no invented score, risk or band."""
    api: AsyncClient = env["api"]
    profile_id = env["profile_id"]
    institution = Institution(
        canonical_name="Unknown Facts University", normalized_name="unknown facts university"
    )
    db = env.get("db_session")
    assert db is not None
    db.add(institution)
    await db.flush()
    program = Program(
        institution_id=institution.id,
        canonical_name="M.Sc. Unknown Facts",
        normalized_name="m.sc. unknown facts",
    )
    db.add(program)
    await db.flush()
    strategy = StrategyRun(
        profile_id=profile_id,
        status=RunStatus.SUCCEEDED,
        scoring_version="v1",
        strategy_version="v1",
        summary="no facts yet",
    )
    db.add(strategy)
    await db.flush()
    db.add(
        ApplicationPlan(
            strategy_run_id=strategy.id,
            profile_id=profile_id,
            program_id=program.id,
            category=ProgramCategory.REACH,
            priority=1,
            rationale="Fit score 0",
        )
    )
    await db.commit()

    response = await api.get(
        f"/api/v1/strategies/{strategy.id}/portfolio", headers=_auth(env["owner_token"])
    )
    assert response.status_code == 200, response.text
    card = response.json()["items"][0]
    assert card["fit_score"] is None
    assert card["reasons"] == []
    assert card["top_risk"] is None
    assert card["estimated_cost"] == {}
    assert card["evidence_freshness"] == {"status": "UNKNOWN", "stale": 0, "total": 0}


# ---------------------------------------------------------- evidence health


async def test_evidence_health_counts_status_confidence_authority_and_unknowns(
    env: dict[str, Any],
) -> None:
    api: AsyncClient = env["api"]
    response = await api.get(
        f"/api/v1/strategies/{env['strategy_id']}/evidence-health",
        headers=_auth(env["owner_token"]),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["programs_total"] == 1
    assert body["programs_with_evidence"] == 1
    assert body["evidence_total"] == 2
    assert body["by_status"] == {"CURRENT": 1, "UNAVAILABLE": 1}
    assert body["by_confidence"] == {"HIGH": 1, "LOW": 1}
    assert body["by_authority"] == {"OFFICIAL_UNIVERSITY": 2}
    assert body["unknowns"] == 1  # the UNAVAILABLE claim
    assert body["conflicting_count"] == 0
    assert body["stale_count"] == 0


async def test_evidence_health_of_a_bare_strategy_reports_zeroes(env: dict[str, Any]) -> None:
    api: AsyncClient = env["api"]
    db = env.get("db_session")
    assert db is not None
    strategy = StrategyRun(
        profile_id=env["profile_id"],
        status=RunStatus.SUCCEEDED,
        scoring_version="v1",
        strategy_version="v1",
        summary="no plans",
    )
    db.add(strategy)
    await db.commit()

    response = await api.get(
        f"/api/v1/strategies/{strategy.id}/evidence-health",
        headers=_auth(env["owner_token"]),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["programs_total"] == 0
    assert body["evidence_total"] == 0
    assert body["by_status"] == {}
    assert body["by_authority"] == {}
    assert body["unknowns"] == 0
    assert body["conflicting_count"] == 0


# --------------------------------------------------------- risk transitions


async def test_patch_risk_acknowledge_resolve_and_dismiss(env: dict[str, Any]) -> None:
    api: AsyncClient = env["api"]
    headers = _auth(env["owner_token"])
    risk_id = env["risk_id"]
    url = f"/api/v1/strategies/{env['strategy_id']}/risks/{risk_id}"

    ack = await api.patch(url, json={"status": "ACKNOWLEDGED"}, headers=headers)
    assert ack.status_code == 200, ack.text
    assert ack.json()["risk"]["status"] == "ACKNOWLEDGED"
    assert ack.json()["risk"]["resolved_at"] is None

    resolved = await api.patch(url, json={"status": "RESOLVED"}, headers=headers)
    assert resolved.status_code == 200, resolved.text
    risk = resolved.json()["risk"]
    assert risk["status"] == "RESOLVED"
    assert risk["resolved_at"] is not None

    # A transition away from RESOLVED clears the resolution timestamp.
    dismissed = await api.patch(url, json={"status": "DISMISSED"}, headers=headers)
    assert dismissed.status_code == 200, dismissed.text
    assert dismissed.json()["risk"]["status"] == "DISMISSED"
    assert dismissed.json()["risk"]["resolved_at"] is None

    # The list endpoint reflects the new state.
    listing = await api.get(f"/api/v1/strategies/{env['strategy_id']}/risks", headers=headers)
    assert listing.status_code == 200
    assert {row["id"]: row["status"] for row in listing.json()["items"]}[str(risk_id)] == "DISMISSED"


async def test_patch_risk_rejects_unknown_states_with_a_422_envelope(
    env: dict[str, Any],
) -> None:
    api: AsyncClient = env["api"]
    url = f"/api/v1/strategies/{env['strategy_id']}/risks/{env['risk_id']}"
    for bad in ("OPEN", "resolved", "", "banana"):
        response = await api.patch(
            url, json={"status": bad}, headers=_auth(env["owner_token"])
        )
        assert response.status_code == 422, f"{bad!r} -> {response.status_code}"
        error = response.json()["error"]
        assert error["code"] == "VALIDATION_ERROR"
        assert "ACKNOWLEDGED" in error["message"]


async def test_patch_risk_is_scoped_to_the_strategy_owner(env: dict[str, Any]) -> None:
    api: AsyncClient = env["api"]
    url = f"/api/v1/strategies/{env['strategy_id']}/risks/{env['risk_id']}"
    response = await api.patch(url, json={"status": "ACKNOWLEDGED"}, headers=_auth(env["other_token"]))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"

    # The owner's risk is untouched by the stranger's attempt.
    risk = await api.get(f"/api/v1/strategies/{env['strategy_id']}/risks", headers=_auth(env["owner_token"]))
    assert {r["status"] for r in risk.json()["items"]} == {"OPEN"}

    # A risk id that does not exist at all: same 404, no probing signal.
    missing = await api.patch(
        f"/api/v1/strategies/{env['strategy_id']}/risks/{uuid.uuid4()}",
        json={"status": "ACKNOWLEDGED"},
        headers=_auth(env["owner_token"]),
    )
    assert missing.status_code == 404


async def test_risk_rows_expose_provenance_fields(env: dict[str, Any]) -> None:
    api: AsyncClient = env["api"]
    response = await api.get(
        f"/api/v1/strategies/{env['strategy_id']}/risks", headers=_auth(env["owner_token"])
    )
    assert response.status_code == 200, response.text
    risk = response.json()["items"][0]
    assert risk["confidence"] == "MEDIUM"
    assert risk["program_id"] == str(env["program_id"])
    assert risk["requirement_id"] is None  # global/unknown provenance stays null
    assert risk["resolved_at"] is None
