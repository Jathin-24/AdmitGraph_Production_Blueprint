"""P2-13: explore search + filters on GET /programs.

Integration tests against real PostgreSQL (fixture: tests/conftest.py).

Every assertion is scoped to a unique per-run tag in the fixture's program
names, so rows seeded by other tests can never leak into these counts.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from app.db.models import (
    Country,
    FitAssessment,
    Institution,
    Intake,
    Program,
    SavedProgram,
    StudentProfile,
)

_ITEM_KEYS = {"id", "name", "country_code", "degree_type", "field_of_study", "official_url"}
_PAGE_KEYS = {"items", "total", "page", "page_size", "next_cursor"}


@pytest.fixture
async def api(db_session: Any) -> AsyncIterator[AsyncClient]:
    from app import main as main_module
    from app.main import app

    main_module._rate_counters.clear()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


async def _ensure_country(db_session: Any, code: str, name: str) -> None:
    if (await db_session.execute(select(Country).where(Country.code == code))).scalars().first():
        return
    db_session.add(Country(code=code, name=name))
    await db_session.flush()


async def _program(
    session: Any,
    *,
    institution_id: uuid.UUID,
    tag: str,
    name: str,
    country: str,
    degree: str,
    city: str,
    canonical: str | None = None,
) -> Program:
    """One catalog row; `canonical` overrides the generated display name."""
    canonical_name = canonical or f"{name} {tag}"
    program = Program(
        institution_id=institution_id,
        canonical_name=canonical_name,
        normalized_name=canonical_name.lower(),
        country_code=country,
        degree_type=degree,
        city=city,
    )
    session.add(program)
    await session.flush()
    return program


@pytest.fixture
async def env(db_session: Any, api: AsyncClient) -> AsyncIterator[dict[str, Any]]:
    """Three tagged programs across two countries/degree levels + one decoy."""
    tag = f"FLT{uuid.uuid4().hex[:8]}"
    response = await api.post(
        "/api/v1/auth/register",
        json={"email": f"{tag.lower()}@example.com", "password": "correct-horse-1"},
    )
    assert response.status_code == 201, response.text
    headers = {"Authorization": f"Bearer {response.json()['token']}"}
    profile = (
        (
            await db_session.execute(
                select(StudentProfile).where(
                    StudentProfile.user_id == uuid.UUID(response.json()["user"]["id"])
                )
            )
        )
        .scalars()
        .one()
    )

    await _ensure_country(db_session, "DE", "Germany")
    await _ensure_country(db_session, "NL", "Netherlands")

    institution = Institution(
        canonical_name=f"Filter University {tag}",
        normalized_name=f"filter university {tag}",
        domain=f"{tag.lower()}.example.test",
    )
    db_session.add(institution)
    await db_session.flush()

    alpha = await _program(
        db_session,
        institution_id=institution.id,
        tag=tag,
        name="M.Sc. Alpha",
        country="DE",
        degree="Master",
        city="Berlin",
    )
    # Deliberately "MScQ…" (no space): `q="MSc_<tag>"` must NOT match it —
    # proof that `_` is escaped rather than treated as a one-char wildcard.
    beta = await _program(
        db_session,
        institution_id=institution.id,
        tag=tag,
        name="",
        canonical=f"MScQ{tag}",
        country="NL",
        degree="Bachelor",
        city="Amsterdam",
    )
    gamma = await _program(
        db_session,
        institution_id=institution.id,
        tag=tag,
        name="M.Sc. Gamma",
        country="DE",
        degree="Master",
        city=f"Cologne {tag}",
    )
    # A fourth program whose name contains both SQL wildcard characters and a
    # literal `%`: `q` must match it as text, never as a pattern.
    percent = await _program(
        db_session,
        institution_id=institution.id,
        tag=tag,
        name="",
        canonical=f"MSc_{tag} 100% Placement",
        country="DE",
        degree="Master",
        city="Hamburg",
    )
    # Decoy: same country/degree, but a DIFFERENT tag — never a `q=tag` hit.
    decoy_institution = Institution(
        canonical_name=f"Other University {uuid.uuid4().hex[:8]}",
        normalized_name=f"other university {uuid.uuid4().hex[:8]}",
        domain=f"other-{uuid.uuid4().hex[:6]}.example.test",
    )
    db_session.add(decoy_institution)
    await db_session.flush()
    decoy = await _program(
        db_session,
        institution_id=decoy_institution.id,
        tag=f"OTHER{uuid.uuid4().hex[:6]}",
        name="M.Sc. Decoy",
        country="DE",
        degree="Master",
        city="Berlin",
    )

    # Deadlines: gamma earliest, alpha next, beta and percent undated.
    db_session.add_all(
        [
            Intake(
                program_id=alpha.id,
                intake_label="Winter",
                intake_year=2027,
                application_deadline=date(2027, 3, 1),
            ),
            Intake(
                program_id=gamma.id,
                intake_label="Winter",
                intake_year=2027,
                application_deadline=date(2027, 1, 15),
            ),
        ]
    )
    # Fit scores for this profile: percent 95 > alpha 80 > beta 70; gamma has
    # NO score, so `sort=fit_score` must park it last (UNKNOWN stays valid).
    db_session.add_all(
        [
            FitAssessment(
                profile_id=profile.id,
                program_id=percent.id,
                overall_score=Decimal("95.00"),
                scoring_version="v1",
                explanation="Fit score 95.00 - this is not an admission probability.",
            ),
            FitAssessment(
                profile_id=profile.id,
                program_id=alpha.id,
                overall_score=Decimal("80.00"),
                scoring_version="v1",
                explanation="Fit score 80.00 - this is not an admission probability.",
            ),
            FitAssessment(
                profile_id=profile.id,
                program_id=beta.id,
                overall_score=Decimal("70.00"),
                scoring_version="v1",
                explanation="Fit score 70.00 - this is not an admission probability.",
            ),
        ]
    )
    db_session.add(SavedProgram(profile_id=profile.id, program_id=beta.id))
    await db_session.commit()

    yield {
        "api": api,
        "db_session": db_session,
        "headers": headers,
        "tag": tag,
        "alpha": str(alpha.id),
        "beta": str(beta.id),
        "gamma": str(gamma.id),
        "percent": str(percent.id),
        "decoy": str(decoy.id),
        "tag_names": {
            alpha.canonical_name,
            beta.canonical_name,
            gamma.canonical_name,
            percent.canonical_name,
        },
    }


async def _get(env: dict[str, Any], **params: Any) -> dict[str, Any]:
    response = await env["api"].get(
        "/api/v1/programs", params=params, headers=env["headers"]
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == _PAGE_KEYS, sorted(body)
    for item in body["items"]:
        assert set(item) == _ITEM_KEYS, sorted(item)
    return body


def _names(body: dict[str, Any]) -> list[str]:
    return [i["name"] for i in body["items"]]


async def test_q_matches_program_university_and_city(env: dict[str, Any]) -> None:
    tag = env["tag"]
    body = await _get(env, q=tag)
    assert body["total"] == 4, (body["total"], _names(body))
    assert set(_names(body)) == env["tag_names"]

    # University name is searched too (all four share the institution).
    by_university = await _get(env, q=f"Filter University {tag}")
    assert by_university["total"] == 4

    # City is searched too; only the tagged Cologne program matches.
    by_city = await _get(env, q=f"Cologne {tag}")
    assert by_city["total"] == 1, _names(by_city)
    assert env["gamma"] == by_city["items"][0]["id"]

    # Case-insensitive.
    assert (await _get(env, q=tag.lower()))["total"] == 4
    assert (await _get(env, q=tag.upper()))["total"] == 4


async def test_q_escapes_like_wildcards(env: dict[str, Any]) -> None:
    """`%`/`_` in `q` are literal characters, never patterns."""
    tag = env["tag"]

    # `_` escaped: only "MSc_<tag>…" matches; the "MScQ<tag>" decoy must not.
    underscore = await _get(env, q=f"MSc_{tag}")
    assert [_["id"] for _ in underscore["items"]] == [env["percent"]], _names(underscore)

    # `%` escaped: every hit is a program whose name literally holds a percent
    # sign (other runs' fixtures share this table, so scope the assertion that
    # way rather than by id).
    percent = await _get(env, q="100% Placement")
    assert env["percent"] in {i["id"] for i in percent["items"]}, _names(percent)
    assert all("%" in i["name"] for i in percent["items"]), _names(percent)

    # A bare `%` must not act as "match everything": everything it returns is
    # a program whose name literally holds a percent sign.
    wildcard = await _get(env, q="%")
    assert env["percent"] in {i["id"] for i in wildcard["items"]}
    assert all("%" in i["name"] for i in wildcard["items"]), _names(wildcard)
    assert wildcard["total"] < 50, "an unescaped % would return the whole catalog"


async def test_country_and_degree_level_filters_are_case_insensitive(env: dict[str, Any]) -> None:
    tag = env["tag"]
    germany = await _get(env, q=tag, country="DE")
    assert {_["id"] for _ in germany["items"]} == {env["alpha"], env["gamma"], env["percent"]}

    # Same filter, lowercase and whitespace-padded.
    padded = await _get(env, q=tag, country=" de ")
    assert {_["id"] for _ in padded["items"]} == {_["id"] for _ in germany["items"]}

    masters = await _get(env, q=tag, degree_level="master")
    assert {_["id"] for _ in masters["items"]} == {env["alpha"], env["gamma"], env["percent"]}

    bachelors = await _get(env, q=tag, degree_level="BACHELOR")
    assert {_["id"] for _ in bachelors["items"]} == {env["beta"]}

    # The two filters combine (and still exclude the untagged decoy).
    both = await _get(env, q=tag, country="de", degree_level="MASTER")
    assert {_["id"] for _ in both["items"]} == {env["alpha"], env["gamma"], env["percent"]}
    assert env["decoy"] not in {_["id"] for _ in both["items"]}


async def test_no_match_returns_an_empty_page(env: dict[str, Any]) -> None:
    body = await _get(env, q=f"NOBODY{uuid.uuid4().hex[:10]}")
    assert body["items"] == []
    assert body["total"] == 0
    assert body["page"] == 1
    assert body["next_cursor"] is None


async def test_sort_by_name_orders_by_program_name(env: dict[str, Any]) -> None:
    """`sort=name` is exactly SQL's ORDER BY canonical_name (collation and
    all), rather than the fit/deadline ordering used by the other sorts."""
    body = await _get(env, q=env["tag"], sort="name")
    names = _names(body)
    assert len(names) == 4
    expected = [
        row[0]
        for row in (
            await env["db_session"].execute(
                text("SELECT canonical_name FROM programs WHERE canonical_name LIKE :p "
                     "ORDER BY canonical_name"),
                {"p": f"%{env['tag']}%"},
            )
        ).all()
    ]
    assert names == expected
    # It is genuinely a different ordering from the default (fit_score).
    fit = await _get(env, q=env["tag"], sort="fit_score")
    assert names != _names(fit)


async def test_sort_by_deadline_puts_undated_programs_last(env: dict[str, Any]) -> None:
    body = await _get(env, q=env["tag"], sort="deadline")
    ids = [_["id"] for _ in body["items"]]
    # gamma (15 Jan) -> alpha (1 Mar) -> the two undated programs last.
    assert ids.index(env["gamma"]) < ids.index(env["alpha"])
    assert ids.index(env["alpha"]) < ids.index(env["beta"])
    assert ids.index(env["alpha"]) < ids.index(env["percent"])


async def test_sort_by_fit_score_puts_unscored_programs_last(env: dict[str, Any]) -> None:
    body = await _get(env, q=env["tag"], sort="fit_score")
    ids = [_["id"] for _ in body["items"]]
    assert ids.index(env["percent"]) < ids.index(env["alpha"]), "95 must outrank 80"
    assert ids.index(env["alpha"]) < ids.index(env["beta"]), "80 must outrank 70"
    assert ids[-1] == env["gamma"], "a program with no score sorts last, not first"


async def test_unknown_sort_value_is_a_422_not_a_500(env: dict[str, Any]) -> None:
    response = await env["api"].get(
        "/api/v1/programs",
        params={"q": env["tag"], "sort": "tuition"},
        headers=env["headers"],
    )
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"


async def test_saved_only_combines_with_filters(env: dict[str, Any]) -> None:
    tag = env["tag"]
    saved = await _get(env, q=tag, saved_only=True)
    assert [_["id"] for _ in saved["items"]] == [env["beta"]]

    # The saved program does not match this country filter.
    saved_germany = await _get(env, q=tag, saved_only=True, country="DE")
    assert saved_germany["items"] == []
    assert saved_germany["total"] == 0


async def test_pagination_still_works_alongside_filters(env: dict[str, Any]) -> None:
    tag = env["tag"]
    first = await _get(env, q=tag, page=1, page_size=3, sort="name")
    assert first["total"] == 4
    assert len(first["items"]) == 3
    assert first["page"] == 1
    assert first["next_cursor"] == "2"

    second = await _get(env, q=tag, page=2, page_size=3, sort="name")
    assert second["total"] == 4
    assert second["page"] == 2
    assert len(second["items"]) == 1
    assert second["next_cursor"] is None, "the last page has no cursor"

    # Pages never overlap.
    overlap = {_["id"] for _ in first["items"]} & {_["id"] for _ in second["items"]}
    assert overlap == set()
    assert len({_["id"] for _ in first["items"]} | {_["id"] for _ in second["items"]}) == 4
