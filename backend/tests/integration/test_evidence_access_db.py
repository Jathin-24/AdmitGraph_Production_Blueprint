"""P1-10: evidence / conflict endpoints are ownership-scoped.

Integration tests against real PostgreSQL (fixture: tests/conftest.py).

The rule under test (app/services/evidence/access.py):

* attached to the caller's profile -> visible;
* attached to ANOTHER real student's profile -> 404 for everyone else,
  including anonymous/demo traffic (404, never 403 — a foreign id must be
  indistinguishable from a non-existent one);
* attached to nobody -> shared corpus: demo mode may read it, a signed-in
  non-owner may not.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.models import (
    ConfidenceLevel,
    Evidence,
    EvidenceConflict,
    EvidenceConflictMember,
    EvidenceStatus,
    Institution,
    Program,
    SavedProgram,
    Source,
    SourceAuthority,
    StudentProfile,
)

# The recheck cooldown is process-global; the autouse fixture below keeps this
# file from consuming slots that later files (test_evidence_recheck_db) still
# expect to be free.


@pytest.fixture(autouse=True)
def _clean_recheck_cooldown() -> Any:
    from app.services.evidence.recheck import reset_recheck_cooldown

    reset_recheck_cooldown()
    yield
    reset_recheck_cooldown()


@pytest.fixture
async def api(db_session: Any) -> AsyncIterator[AsyncClient]:
    from app import main as main_module
    from app.main import app

    main_module._rate_counters.clear()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


async def _register(api: AsyncClient, prefix: str) -> tuple[str, str]:
    response = await api.post(
        "/api/v1/auth/register",
        json={"email": f"{prefix}-{uuid.uuid4().hex[:8]}@example.com", "password": "correct-horse-1"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return str(body["token"]), str(body["user"]["id"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _profile_id(db_session: Any, user_id: str) -> uuid.UUID:
    profile = (
        (
            await db_session.execute(
                select(StudentProfile).where(StudentProfile.user_id == uuid.UUID(user_id))
            )
        )
        .scalars()
        .one()
    )
    return profile.id


async def _seed_evidence(
    db_session: Any, *, subject_id: uuid.UUID, claim: str
) -> Evidence:
    now = datetime.now(UTC)
    source = Source(
        url=f"https://access-{uuid.uuid4().hex[:8]}.example.test/page",
        canonical_url=f"https://access-{uuid.uuid4().hex[:8]}.example.test/page",
        domain=f"access-{uuid.uuid4().hex[:8]}.example.test",
        title="Access fixture",
        source_authority=SourceAuthority.OFFICIAL_UNIVERSITY,
        last_seen_at=now,
    )
    db_session.add(source)
    await db_session.flush()
    evidence = Evidence(
        source_id=source.id,
        claim_type="language",
        subject_type="program",
        subject_id=subject_id,
        claim=claim,
        normalized_claim="ielts_overall_min",
        extracted_value={"min": 6.5, "test": "IELTS"},
        confidence=ConfidenceLevel.HIGH,
        status=EvidenceStatus.CURRENT,
        retrieved_at=now,
        freshness_deadline=now + timedelta(days=30),
        extraction_version="v1",
    )
    db_session.add(evidence)
    await db_session.flush()
    return evidence


@pytest.fixture
async def env(db_session: Any, api: AsyncClient) -> AsyncIterator[dict[str, Any]]:
    """An owner whose saved program carries evidence, plus an unclaimed row."""
    owner_token, owner_user = await _register(api, "access-owner")
    other_token, other_user = await _register(api, "access-other")
    owner_profile = await _profile_id(db_session, owner_user)

    institution = Institution(
        canonical_name=f"Access University {uuid.uuid4().hex[:6]}",
        normalized_name=f"access university {uuid.uuid4().hex[:6]}",
        domain=f"access-{uuid.uuid4().hex[:6]}.example.test",
    )
    db_session.add(institution)
    await db_session.flush()

    owned_program = Program(
        institution_id=institution.id,
        canonical_name=f"M.Sc. Owned {uuid.uuid4().hex[:6]}",
        normalized_name=f"m.sc. owned {uuid.uuid4().hex[:6]}",
    )
    unowned_program = Program(
        institution_id=institution.id,
        canonical_name=f"M.Sc. Unowned {uuid.uuid4().hex[:6]}",
        normalized_name=f"m.sc. unowned {uuid.uuid4().hex[:6]}",
    )
    db_session.add_all([owned_program, unowned_program])
    await db_session.flush()

    # Attaching the program to the owner is what makes its claims theirs.
    db_session.add(SavedProgram(profile_id=owner_profile, program_id=owned_program.id))

    owned_evidence = await _seed_evidence(
        db_session, subject_id=owned_program.id, claim="Owner's page: IELTS 6.5."
    )
    unowned_evidence = await _seed_evidence(
        db_session, subject_id=unowned_program.id, claim="Unclaimed page: IELTS 7.0."
    )

    conflict = EvidenceConflict(
        conflict_key=f"program:{owned_program.id}:ielts_overall_min",
        description="Owner's claims disagree",
        resolution_status="UNRESOLVED",
    )
    db_session.add(conflict)
    await db_session.flush()
    db_session.add(
        EvidenceConflictMember(conflict_id=conflict.id, evidence_id=owned_evidence.id)
    )
    await db_session.commit()

    yield {
        "api": api,
        "owner_headers": _auth(owner_token),
        "other_headers": _auth(other_token),
        "owned_evidence_id": str(owned_evidence.id),
        "unowned_evidence_id": str(unowned_evidence.id),
        "conflict_id": str(conflict.id),
        "owned_program_id": str(owned_program.id),
    }


async def test_owner_reads_their_own_evidence(env: dict[str, Any]) -> None:
    response = await env["api"].get(
        f"/api/v1/evidence/{env['owned_evidence_id']}", headers=env["owner_headers"]
    )
    assert response.status_code == 200, response.text
    assert response.json()["id"] == env["owned_evidence_id"]


async def test_another_user_cannot_read_someone_elses_evidence(env: dict[str, Any]) -> None:
    response = await env["api"].get(
        f"/api/v1/evidence/{env['owned_evidence_id']}", headers=env["other_headers"]
    )
    assert response.status_code == 404, response.text
    error = response.json()["error"]
    assert error["code"] == "NOT_FOUND"


async def test_anonymous_cannot_read_claimed_evidence(env: dict[str, Any]) -> None:
    """Unauthenticated (demo) traffic must not read a real student's claims."""
    response = await env["api"].get(f"/api/v1/evidence/{env['owned_evidence_id']}")
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_anonymous_reads_unclaimed_evidence(env: dict[str, Any]) -> None:
    """Evidence nobody attached is the shared corpus — still readable."""
    response = await env["api"].get(f"/api/v1/evidence/{env['unowned_evidence_id']}")
    assert response.status_code == 200, response.text
    assert response.json()["id"] == env["unowned_evidence_id"]


async def test_signed_in_non_owner_cannot_read_unclaimed_evidence(
    env: dict[str, Any],
) -> None:
    """Demo mode is anonymous/demo only: a signed-in stranger gets a 404."""
    response = await env["api"].get(
        f"/api/v1/evidence/{env['unowned_evidence_id']}", headers=env["other_headers"]
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_missing_evidence_is_a_404_too(env: dict[str, Any]) -> None:
    response = await env["api"].get(f"/api/v1/evidence/{uuid.uuid4()}", headers=env["owner_headers"])
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_recheck_reaches_the_owner_but_404s_for_everyone_else(
    env: dict[str, Any],
) -> None:
    """Error order: 404 (out of scope) beats 409 (provider unavailable)."""
    api: AsyncClient = env["api"]

    foreign = await api.post(
        f"/api/v1/evidence/{env['owned_evidence_id']}/recheck", headers=env["other_headers"]
    )
    assert foreign.status_code == 404, foreign.text
    assert foreign.json()["error"]["code"] == "NOT_FOUND"

    anonymous = await api.post(f"/api/v1/evidence/{env['owned_evidence_id']}/recheck")
    assert anonymous.status_code == 404, anonymous.text

    # The owner passes the access gate and reaches the provider check (no key
    # configured in the test run -> 409, never a fake refresh).
    owner = await api.post(
        f"/api/v1/evidence/{env['owned_evidence_id']}/recheck", headers=env["owner_headers"]
    )
    assert owner.status_code == 409, owner.text
    assert owner.json()["error"]["code"] == "PROVIDER_UNAVAILABLE"


async def test_owner_resolves_their_conflict(env: dict[str, Any]) -> None:
    response = await env["api"].post(
        f"/api/v1/evidence/conflicts/{env['conflict_id']}/resolve",
        json={"reason": "Official page wins"},
        headers=env["owner_headers"],
    )
    assert response.status_code == 200, response.text
    assert response.json()["resolution_status"] == "RESOLVED"


async def test_strangers_cannot_resolve_a_claimed_conflict(env: dict[str, Any]) -> None:
    api: AsyncClient = env["api"]
    for headers in (env["other_headers"], None):
        kwargs = {"headers": headers} if headers else {}
        response = await api.post(
            f"/api/v1/evidence/conflicts/{env['conflict_id']}/resolve",
            json={"reason": "Not mine to decide"},
            **kwargs,
        )
        assert response.status_code == 404, response.text
        assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_unknown_conflict_is_a_404(env: dict[str, Any]) -> None:
    response = await env["api"].post(
        f"/api/v1/evidence/conflicts/{uuid.uuid4()}/resolve",
        json={"reason": "x"},
        headers=env["owner_headers"],
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "NOT_FOUND"


# --------------------------------------------------------------- handoff
# W3 left the LIST endpoints unscoped while single-read/recheck/resolve are
# ownership-checked. Close it with the same rule from
# app/services/evidence/access.py (reused, not duplicated).


async def test_conflicts_list_is_scoped_like_single_read(env: dict[str, Any]) -> None:
    """GET /evidence/{id}/conflicts: owner 200, everyone else 404."""
    api: AsyncClient = env["api"]
    path = f"/api/v1/evidence/{env['owned_evidence_id']}/conflicts"

    owner = await api.get(path, headers=env["owner_headers"])
    assert owner.status_code == 200, owner.text
    assert owner.json()["evidence_id"] == env["owned_evidence_id"]
    assert owner.json()["conflicts"], "the owner must see the conflict group"

    # Signed-in non-owner AND anonymous (demo mode cannot read a real
    # student's claims) — both 404, never 403.
    for headers in (env["other_headers"], None):
        kwargs = {"headers": headers} if headers else {}
        foreign = await api.get(path, **kwargs)
        assert foreign.status_code == 404, foreign.text
        assert foreign.json()["error"]["code"] == "NOT_FOUND"

    # An unknown id is indistinguishable from a foreign one.
    unknown = await api.get(
        f"/api/v1/evidence/{uuid.uuid4()}/conflicts", headers=env["owner_headers"]
    )
    assert unknown.status_code == 404, unknown.text
    assert unknown.json()["error"]["code"] == "NOT_FOUND"


async def test_conflicts_list_keeps_the_shared_corpus_rule(env: dict[str, Any]) -> None:
    """Unclaimed evidence: demo mode may list its conflicts, strangers may not."""
    api: AsyncClient = env["api"]
    path = f"/api/v1/evidence/{env['unowned_evidence_id']}/conflicts"

    anonymous = await api.get(path)
    assert anonymous.status_code == 200, anonymous.text
    assert anonymous.json()["conflicts"] == []  # no conflict group was seeded

    stranger = await api.get(path, headers=env["other_headers"])
    assert stranger.status_code == 404, stranger.text
    assert stranger.json()["error"]["code"] == "NOT_FOUND"


async def test_evidence_list_is_scoped_to_the_caller(env: dict[str, Any]) -> None:
    """GET /evidence (filtered and not) never lists another student's rows."""
    api: AsyncClient = env["api"]
    owned = env["owned_evidence_id"]
    unowned = env["unowned_evidence_id"]
    program_id = env["owned_program_id"]

    # --- program-filtered list ------------------------------------------------
    mine = await api.get(
        "/api/v1/evidence", params={"program_id": program_id}, headers=env["owner_headers"]
    )
    assert mine.status_code == 200, mine.text
    assert owned in {item["id"] for item in mine.json()["items"]}

    for headers in (env["other_headers"], None):
        kwargs = {"headers": headers} if headers else {}
        their_view = await api.get(
            "/api/v1/evidence", params={"program_id": program_id}, **kwargs
        )
        assert their_view.status_code == 200, their_view.text
        assert their_view.json()["items"] == [], "foreign program evidence must not be listed"

    # --- unfiltered list (rows are newest-first, both fixtures are fresh) -----
    owner_all = await api.get("/api/v1/evidence", headers=env["owner_headers"])
    assert owned in {item["id"] for item in owner_all.json()["items"]}

    stranger_all = await api.get("/api/v1/evidence", headers=env["other_headers"])
    stranger_ids = {item["id"] for item in stranger_all.json()["items"]}
    assert owned not in stranger_ids, "another student's claims leaked into a list"

    anonymous_all = await api.get("/api/v1/evidence")
    assert anonymous_all.status_code == 200, anonymous_all.text
    anonymous_ids = {item["id"] for item in anonymous_all.json()["items"]}
    assert owned not in anonymous_ids  # claimed by a real student → hidden
    assert unowned in anonymous_ids  # unclaimed → shared demo corpus
