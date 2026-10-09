"""Audit D-6: the daily search budget covers every provider-spend endpoint.

`reject_if_budget_spent` (app/core/budget.py) used to run only for research
plan/run creation, but `POST /evidence/{id}/recheck` and
`POST /monitor/subscriptions/{id}/check` also burn SerpApi credits. Both now
answer `429 BUDGET_EXCEEDED` before any provider call, keeping the error
order the API already promises: 404 ownership -> 429 rate-limit (cooldown) ->
429 budget -> 409 provider.

Budgets are set relative to whatever rows earlier suites already inserted in
the shared scratch database, so these tests are order-independent.

Integration test against real PostgreSQL (fixture: tests/conftest.py).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.core.budget import utc_day_start
from app.core.config import get_settings
from app.db.models import (
    ConfidenceLevel,
    Evidence,
    EvidenceStatus,
    Institution,
    MonitorSnapshot,
    MonitorSubscription,
    Program,
    RunStatus,
    SavedProgram,
    SearchRun,
    Source,
    SourceAuthority,
    StudentProfile,
)

SEED_PREFIX = "budget-guards-"


@pytest.fixture(autouse=True)
async def _clean_process_state(db_session: Any) -> AsyncIterator[None]:
    """Rate-limit counters and the recheck cooldown are process-global.

    The ``search_runs`` rows this file seeds (tagged ``SEED_PREFIX``) are
    removed on teardown so later suites count the same daily spend.
    Settings themselves are restored by ``monkeypatch`` (per test).
    """
    from app import main as main_module
    from app.services.evidence.recheck import reset_recheck_cooldown

    main_module._rate_counters.clear()
    reset_recheck_cooldown()
    yield
    main_module._rate_counters.clear()
    reset_recheck_cooldown()
    await db_session.execute(
        delete(SearchRun).where(SearchRun.query.startswith(SEED_PREFIX))
    )
    await db_session.commit()


@pytest.fixture
async def api(db_session: Any) -> AsyncIterator[AsyncClient]:
    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


async def _register(api: AsyncClient, prefix: str) -> tuple[str, str]:
    response = await api.post(
        "/api/v1/auth/register",
        json={
            "email": f"{prefix}-{uuid.uuid4().hex[:8]}@example.com",
            "password": "correct-horse-1",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return str(body["token"]), str(body["user"]["id"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _profile_id(db_session: Any, user_id: str) -> uuid.UUID:
    profile = (
        (await db_session.execute(select(StudentProfile).where(StudentProfile.user_id == uuid.UUID(user_id))))
        .scalars()
        .one()
    )
    return profile.id


async def _today_count(db_session: Any) -> int:
    stmt = select(SearchRun).where(SearchRun.requested_at >= utc_day_start())
    rows = (await db_session.execute(stmt)).scalars().all()
    return len(rows)


async def _exhaust_budget(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> int:
    """Set the global budget exactly to today's spend (>= 1 row, or a seed)."""
    db_session.add(
        SearchRun(engine="google", query=f"{SEED_PREFIX}{uuid.uuid4().hex[:8]}", status=RunStatus.SUCCEEDED)
    )
    await db_session.commit()
    spent = await _today_count(db_session)
    monkeypatch.setattr(get_settings(), "daily_search_budget", spent)
    monkeypatch.setattr(get_settings(), "daily_search_budget_per_user", 0)
    return spent


async def _open_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """Leave room for every request this test file makes."""
    monkeypatch.setattr(get_settings(), "daily_search_budget", 10_000)
    monkeypatch.setattr(get_settings(), "daily_search_budget_per_user", 0)


async def _seed_owned_evidence(db_session: Any, user_id: str) -> str:
    """Evidence attached to a program the caller saved (owner-scoped)."""
    now = datetime.now(UTC)
    profile_id = await _profile_id(db_session, user_id)
    tag = uuid.uuid4().hex[:8]
    source = Source(
        url=f"https://budget-{tag}.example.test/page",
        canonical_url=f"https://budget-{tag}.example.test/page",
        domain=f"budget-{tag}.example.test",
        title="Budget fixture",
        source_authority=SourceAuthority.OFFICIAL_UNIVERSITY,
        last_seen_at=now,
    )
    db_session.add(source)
    await db_session.flush()
    institution = Institution(
        canonical_name=f"Budget University {tag}",
        normalized_name=f"budget university {tag}",
        domain=f"budget-{tag}.example.test",
    )
    db_session.add(institution)
    await db_session.flush()
    program = Program(
        institution_id=institution.id,
        canonical_name=f"M.Sc. Budget {tag}",
        normalized_name=f"m.sc. budget {tag}",
    )
    db_session.add(program)
    await db_session.flush()
    db_session.add(SavedProgram(profile_id=profile_id, program_id=program.id))
    evidence = Evidence(
        source_id=source.id,
        claim_type="language",
        subject_type="program",
        subject_id=program.id,
        claim="Budget fixture page: IELTS 6.5.",
        normalized_claim="ielts_overall_min",
        extracted_value={"min": 6.5, "test": "IELTS"},
        confidence=ConfidenceLevel.HIGH,
        status=EvidenceStatus.STALE,
        retrieved_at=now - timedelta(days=40),
        freshness_deadline=now - timedelta(days=10),
        extraction_version="v1",
    )
    db_session.add(evidence)
    await db_session.commit()
    return str(evidence.id)


# ---------------------------------------------------------------------------
# POST /evidence/{id}/recheck
# ---------------------------------------------------------------------------


async def test_recheck_at_the_budget_is_refused_before_any_provider_call(
    db_session: Any,
    api: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token, user_id = await _register(api, "budget-recheck")
    evidence_id = await _seed_owned_evidence(db_session, user_id)
    await _exhaust_budget(db_session, monkeypatch)
    runs_before = await _today_count(db_session)

    blocked = await api.post(f"/api/v1/evidence/{evidence_id}/recheck", headers=_auth(token))

    assert blocked.status_code == 429, blocked.text
    error = blocked.json()["error"]
    assert error["code"] == "BUDGET_EXCEEDED"
    assert "budget" in error["message"].lower()  # plain language, not a code dump
    assert await _today_count(db_session) == runs_before, "no provider call may be recorded"

    # The blocked attempt must not have consumed the caller's recheck cooldown:
    # with budget reopened, the request reaches the provider gate (409 — no
    # SERPAPI_API_KEY in this run) instead of a RATE_LIMITED cooldown.
    await _open_budget(monkeypatch)
    allowed = await api.post(f"/api/v1/evidence/{evidence_id}/recheck", headers=_auth(token))
    assert allowed.status_code == 409, allowed.text
    assert allowed.json()["error"]["code"] == "PROVIDER_UNAVAILABLE"


async def test_recheck_under_the_budget_reaches_the_provider_gate(
    db_session: Any,
    api: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token, user_id = await _register(api, "budget-recheck-open")
    evidence_id = await _seed_owned_evidence(db_session, user_id)
    await _open_budget(monkeypatch)

    response = await api.post(f"/api/v1/evidence/{evidence_id}/recheck", headers=_auth(token))

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "PROVIDER_UNAVAILABLE"
    assert response.status_code != 429


async def test_recheck_ownership_404_outranks_an_exhausted_budget(
    db_session: Any,
    api: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """404 ownership first: a foreign id stays indistinguishable from a miss."""
    owner_token, owner_id = await _register(api, "budget-recheck-owner")
    other_token, _other_id = await _register(api, "budget-recheck-other")
    evidence_id = await _seed_owned_evidence(db_session, owner_id)
    await _exhaust_budget(db_session, monkeypatch)

    foreign = await api.post(f"/api/v1/evidence/{evidence_id}/recheck", headers=_auth(other_token))
    missing = await api.post(
        f"/api/v1/evidence/{uuid.uuid4()}/recheck", headers=_auth(other_token)
    )
    # The owner (correctly) hits the budget instead.
    owner = await api.post(f"/api/v1/evidence/{evidence_id}/recheck", headers=_auth(owner_token))

    assert foreign.status_code == 404, foreign.text
    assert foreign.json()["error"]["code"] == "NOT_FOUND"
    assert missing.status_code == 404, missing.text
    assert owner.status_code == 429, owner.text
    assert owner.json()["error"]["code"] == "BUDGET_EXCEEDED"


async def test_recheck_rate_limit_outranks_the_budget(
    db_session: Any,
    api: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Historical order kept: 404 -> 429 RATE_LIMITED -> 429 BUDGET -> 409."""
    from app.services.evidence.recheck import RECHECK_MAX_PER_WINDOW

    token, user_id = await _register(api, "budget-recheck-cooldown")
    evidence_id = await _seed_owned_evidence(db_session, user_id)
    await _open_budget(monkeypatch)

    # Fill the per-caller cooldown window (each attempt ends at 409 — no key).
    for _ in range(RECHECK_MAX_PER_WINDOW):
        response = await api.post(f"/api/v1/evidence/{evidence_id}/recheck", headers=_auth(token))
        assert response.status_code == 409, response.text
    rate_limited = await api.post(
        f"/api/v1/evidence/{evidence_id}/recheck", headers=_auth(token)
    )
    assert rate_limited.status_code == 429, rate_limited.text
    assert rate_limited.json()["error"]["code"] == "RATE_LIMITED"

    # Now the budget is gone too: the earlier gate (cooldown) still answers.
    await _exhaust_budget(db_session, monkeypatch)
    still_rate_limited = await api.post(
        f"/api/v1/evidence/{evidence_id}/recheck", headers=_auth(token)
    )
    assert still_rate_limited.status_code == 429, still_rate_limited.text
    assert still_rate_limited.json()["error"]["code"] == "RATE_LIMITED"


# ---------------------------------------------------------------------------
# POST /monitor/subscriptions/{id}/check
# ---------------------------------------------------------------------------


@pytest.fixture
async def monitor_env(
    db_session: Any, api: AsyncClient
) -> AsyncIterator[tuple[str, str, str]]:
    """(owner token, other user's token, subscription id) with teardown."""
    owner_token, _owner_id = await _register(api, "budget-monitor")
    other_token, _other_id = await _register(api, "budget-monitor-other")
    created = await api.post(
        "/api/v1/monitor/subscriptions",
        json={"field_key": "deadline", "frequency": "WEEKLY"},
        headers=_auth(owner_token),
    )
    assert created.status_code == 200, created.text
    subscription_id = created.json()["id"]
    yield owner_token, other_token, subscription_id

    snapshots = (
        await db_session.execute(
            select(MonitorSnapshot).where(
                MonitorSnapshot.subscription_id == uuid.UUID(subscription_id)
            )
        )
    ).scalars().all()
    for snapshot in snapshots:
        await db_session.delete(snapshot)
    await db_session.execute(
        delete(MonitorSubscription).where(MonitorSubscription.id == uuid.UUID(subscription_id))
    )
    await db_session.commit()


async def test_monitor_check_at_the_budget_is_refused_before_any_provider_call(
    db_session: Any,
    api: AsyncClient,
    monitor_env: tuple[str, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_token, _other_token, subscription_id = monitor_env
    await _exhaust_budget(db_session, monkeypatch)
    runs_before = await _today_count(db_session)

    blocked = await api.post(
        f"/api/v1/monitor/subscriptions/{subscription_id}/check", headers=_auth(owner_token)
    )

    assert blocked.status_code == 429, blocked.text
    error = blocked.json()["error"]
    assert error["code"] == "BUDGET_EXCEEDED"
    assert "budget" in error["message"].lower()
    assert await _today_count(db_session) == runs_before, "no provider call may run"
    snapshots = (
        await db_session.execute(
            select(MonitorSnapshot).where(
                MonitorSnapshot.subscription_id == uuid.UUID(subscription_id)
            )
        )
    ).scalars().all()
    assert snapshots == [], "a refused check must write no snapshot"


async def test_monitor_check_under_the_budget_runs(
    db_session: Any,
    api: AsyncClient,
    monitor_env: tuple[str, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_token, _other_token, subscription_id = monitor_env
    await _open_budget(monkeypatch)

    response = await api.post(
        f"/api/v1/monitor/subscriptions/{subscription_id}/check", headers=_auth(owner_token)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert {"id", "change_type", "material_change"} <= set(body)
    assert response.status_code != 429


async def test_monitor_check_ownership_404_outranks_an_exhausted_budget(
    db_session: Any,
    api: AsyncClient,
    monitor_env: tuple[str, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _owner_token, other_token, subscription_id = monitor_env
    await _exhaust_budget(db_session, monkeypatch)

    foreign = await api.post(
        f"/api/v1/monitor/subscriptions/{subscription_id}/check", headers=_auth(other_token)
    )
    missing = await api.post(
        f"/api/v1/monitor/subscriptions/{uuid.uuid4()}/check", headers=_auth(other_token)
    )

    assert foreign.status_code == 404, foreign.text
    assert foreign.json()["error"]["code"] == "NOT_FOUND"
    assert missing.status_code == 404, missing.text
