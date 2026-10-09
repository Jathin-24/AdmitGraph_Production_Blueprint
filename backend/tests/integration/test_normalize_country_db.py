"""Audit D-2: the live pipeline writes `programs.country_code` / `degree_type`.

`normalize_programs` used to insert name-only Program rows, so the Explore
page's filter columns were always NULL and `GET /programs?country=DE`
answered `total: 0`. The step now derives:

* ``country_code`` from the discovery run's recorded SerpApi localization
  (``SearchRun.parameters.gl``, falling back to the step's locale) — and
  inserts the ``countries`` reference row it needs for the FK;
* ``degree_type`` from the deterministic title keyword rule
  (``derive_degree_type``);

and backfills already-normalized rows without ever overwriting a value.
Anything that cannot be derived stays NULL (never a guess).

Integration tests against real PostgreSQL (fixture: tests/conftest.py).
No provider calls: the program/funding waves are stubbed, so only stored
rows are written.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.db.models import (
    Country,
    Institution,
    Program,
    ResearchPlan,
    RunStatus,
    SearchResult,
    SearchRun,
    Source,
    SourceAuthority,
)
from app.services.profile import get_or_create_profile
from app.services.research.planner import COUNTRY_NAMES


@pytest.fixture
async def api(db_session: Any) -> AsyncIterator[AsyncClient]:
    """HTTP client wired to the ASGI app (same scratch database as db_session)."""
    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


@pytest.fixture
async def env(db_session: Any, api: AsyncClient) -> AsyncIterator[dict[str, Any]]:
    """Per-test tag plus teardown: everything this module seeds is removed.

    The scratch database is shared across suites — a program (or discovery
    run) left behind here would enter a later run's shortlist/window and
    change another suite's counts.
    """
    tag = uuid.uuid4().hex[:8]
    env: dict[str, Any] = {"tag": tag, "session": db_session, "api": api, "plans": []}
    yield env

    if env["plans"]:
        await db_session.execute(
            delete(ResearchPlan).where(ResearchPlan.id.in_(env["plans"]))
        )
    run_ids = (
        (
            await db_session.execute(
                select(SearchRun.id).where(SearchRun.query.startswith(f"normcountry-{tag}"))
            )
        )
        .scalars()
        .all()
    )
    if run_ids:
        await db_session.execute(
            delete(SearchResult).where(SearchResult.search_run_id.in_(run_ids))
        )
        await db_session.execute(delete(SearchRun).where(SearchRun.id.in_(run_ids)))
    await db_session.execute(
        delete(Program).where(Program.normalized_name.like(f"%{tag}%"))
    )
    domain = f"norm-{tag}.example.test"
    await db_session.execute(delete(Institution).where(Institution.domain == domain))
    await db_session.execute(delete(Source).where(Source.domain == domain))
    await db_session.commit()


def _stub_waves(monkeypatch: pytest.MonkeyPatch) -> None:
    """Program/funding waves make provider searches — never in this module."""
    from app.services.research.orchestrator import ResearchService

    async def _stub(self: Any, session: Any, plan: Any, shortlist: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(ResearchService, "_run_program_queries", _stub)
    monkeypatch.setattr(ResearchService, "_run_funding_queries", _stub)


async def _ensure_country(session: Any, code: str) -> None:
    """The `countries` reference table ships empty; seed the row if needed."""
    name = COUNTRY_NAMES[code]
    found = (await session.execute(select(Country.code).where(Country.code == code))).first()
    if found is None:
        session.add(Country(code=code, name=name))
        await session.commit()


async def _seed_discovery(
    session: Any,
    *,
    tag: str,
    title: str,
    gl: str | None = "de",
    position: int = 0,
) -> None:
    """One discovery result as the search step records it (localization included)."""
    now = datetime.now(UTC)
    domain = f"norm-{tag}.example.test"
    source = Source(
        url=f"https://{domain}/program",
        canonical_url=f"https://{domain}/program-{uuid.uuid4().hex[:6]}",
        domain=domain,
        title=title,
        source_authority=SourceAuthority.CREDIBLE_SECONDARY,
        last_seen_at=now,
    )
    session.add(source)
    await session.flush()
    parameters: dict[str, Any] = {"purpose": "discovery"}
    if gl is not None:
        parameters["gl"] = gl
    run = SearchRun(
        engine="google",
        query=f"normcountry-{tag}-{uuid.uuid4().hex[:6]}",
        parameters=parameters,
        status=RunStatus.SUCCEEDED,
    )
    session.add(run)
    await session.flush()
    session.add(
        SearchResult(
            search_run_id=run.id,
            source_id=source.id,
            position=position,
            result_type="google",
            title=title,
            snippet="Stored discovery result.",
            result_url=source.url,
            raw_payload={},
            retrieved_at=now,
        )
    )
    await session.commit()


async def _seed_program(session: Any, *, tag: str, name: str, country: str | None,
                        degree: str | None) -> Program:
    """An already-normalized row (exercises the backfill branch)."""
    domain = f"norm-{tag}.example.test"
    institution = (
        (
            await session.execute(
                select(Institution).where(Institution.normalized_name == domain)
            )
        )
        .scalars()
        .first()
    )
    if institution is None:
        institution = Institution(
            canonical_name=f"Norm Probe {tag}", normalized_name=domain, domain=domain
        )
        session.add(institution)
        await session.flush()
    program = Program(
        institution_id=institution.id,
        canonical_name=name,
        normalized_name=name.strip().lower(),
        country_code=country,
        degree_type=degree,
    )
    session.add(program)
    await session.commit()
    return program


async def _plan(session: Any, env: dict[str, Any]) -> ResearchPlan:
    profile = await get_or_create_profile(session)
    plan = ResearchPlan(
        profile_id=profile.id, requested_goal={"intake_year": 2027}, status=RunStatus.QUEUED
    )
    session.add(plan)
    await session.commit()
    env["plans"].append(plan.id)
    return plan


async def _run_normalize(
    session: Any, env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    from app.services.research.orchestrator import ResearchService

    _stub_waves(monkeypatch)
    service = ResearchService()
    plan = await _plan(session, env)
    return await service._normalize_programs(session, plan)


async def _program_by_name(session: Any, name: str) -> Program:
    row = (
        await session.execute(select(Program).where(Program.normalized_name == name.strip().lower()))
    ).scalars()
    program = row.first()
    assert program is not None, f"normalize must store {name!r}"
    return program


async def test_discovery_run_writes_country_and_degree(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run localized to `gl=de` produces a filterable program row."""
    session, api, tag = env["session"], env["api"], env["tag"]
    title = f"M.Sc. Country Probe {tag}"
    await _seed_discovery(session, tag=tag, title=title, gl="de")

    output = await _run_normalize(session, env, monkeypatch)
    assert output["programs_created"] >= 1

    session.expire_all()
    program = await _program_by_name(session, title)
    assert program.country_code == "DE", "the recorded search locale must reach the column"
    assert program.degree_type == "MASTERS", "the title names a master's level"
    # FK-safe: the reference row ships with the code (countries starts empty).
    assert (
        await session.execute(select(Country.code).where(Country.code == "DE"))
    ).first() is not None

    # End-to-end: the Explore filter now finds it.
    response = await api.get("/api/v1/programs", params={"q": tag, "country": "DE"})
    assert response.status_code == 200, response.text
    items = [i for i in response.json()["items"] if i["name"] == title]
    assert items, response.text
    assert items[0]["country_code"] == "DE"
    assert items[0]["degree_type"] == "MASTERS"


async def test_country_and_degree_stay_null_when_not_determinable(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A code we cannot name factually, and a title with no level -> NULL.

    NULL beats a fabricated country/degree (audit: never guess a fact).
    """
    session, api, tag = env["session"], env["api"], env["tag"]
    title = f"Degree Programme in Country Probe {tag}"  # names no level
    await _seed_discovery(session, tag=tag, title=title, gl="zz")  # not in COUNTRY_NAMES

    await _run_normalize(session, env, monkeypatch)

    session.expire_all()
    program = await _program_by_name(session, title)
    assert program.country_code is None, "an unnameable code must never become a country"
    assert program.degree_type is None, "no deterministic rule -> NULL, not a guess"
    # The fabricated pair is invisible to the filter (nothing to return).
    response = await api.get("/api/v1/programs", params={"q": tag, "country": "ZZ"})
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []


async def test_run_without_gl_falls_back_to_the_step_locale(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A legacy/failed run recorded no per-query `gl`: the step locale applies."""
    from app.services.research.orchestrator import ResearchService

    session, tag = env["session"], env["tag"]
    title = f"M.Sc. Locale Probe {tag}"
    await _seed_discovery(session, tag=tag, title=title, gl=None)

    _stub_waves(monkeypatch)
    service = ResearchService()
    service._search_locale = {"gl": "de", "hl": "en"}
    plan = await _plan(session, env)
    await service._normalize_programs(session, plan)

    session.expire_all()
    program = await _program_by_name(session, title)
    assert program.country_code == "DE"
    assert program.degree_type == "MASTERS"


async def test_backfill_fills_missing_filters_but_never_overwrites(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Name-only rows from earlier runs gain the columns; written values stay."""
    session, tag = env["session"], env["tag"]
    await _ensure_country(session, "NL")
    missing = f"M.Sc. Backfill Probe {tag}"
    written = f"PhD Never Overwrite {tag}"
    await _seed_program(session, tag=tag, name=missing, country=None, degree=None)
    await _seed_program(session, tag=tag, name=written, country="NL", degree="PHD")
    await _seed_discovery(session, tag=tag, title=missing, gl="de", position=0)
    await _seed_discovery(session, tag=tag, title=written, gl="de", position=1)

    await _run_normalize(session, env, monkeypatch)

    session.expire_all()
    # The dedup path ran: the discovery rows updated the existing rows and
    # produced no duplicate catalog entries.
    for name in (missing, written):
        rows = (
            await session.execute(
                select(Program).where(Program.normalized_name == name.strip().lower())
            )
        ).scalars().all()
        assert len(rows) == 1, f"{name!r} must not be duplicated"
    filled = await _program_by_name(session, missing)
    assert filled.country_code == "DE", "a NULL country is backfilled from the run's locale"
    assert filled.degree_type == "MASTERS", "a NULL degree is backfilled from the title"
    untouched = await _program_by_name(session, written)
    assert untouched.country_code == "NL", "an existing country is never overwritten"
    assert untouched.degree_type == "PHD", "an existing degree is never overwritten"
