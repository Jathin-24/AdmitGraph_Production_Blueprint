"""Failure simulator: scenario transforms, re-tiering, and delta math.

DB-backed tests build a minimal strategy (institutions, programs, requirements,
fit assessments, plan rows) in the scratch database, then re-score it through
the real scoring/selection helpers — the same code path POST
/strategies/{id}/simulate uses. Nothing here may write ApplicationPlan rows.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import select

from app.api.v1.strategies import _simulation_base, simulate
from app.db.models import (
    ApplicationPlan,
    CounterfactualRun,
    Country,
    FitAssessment,
    Institution,
    ProfilePreference,
    Program,
    ProgramCategory,
    Requirement,
    ResearchPlan,
    RunStatus,
    StrategyRun,
)

# Aliased: pytest must not try to collect the SQLAlchemy model `TestScore` as a
# test class ("cannot collect test class 'TestScore' because it has a __init__").
from app.db.models import TestScore as StudentTestScore
from app.services.profile import get_or_create_profile
from app.services.strategy.simulator import (
    SCENARIOS,
    _facts_from,
    _language_score,
    apply_scenario,
    compute_delta,
    recompute_portfolio,
)

# ---------------------------------------------------------------- unit tests


def test_budget_ielts_remove_scenarios_still_transform() -> None:
    assert (
        apply_scenario({"total_budget_amount": 1800000}, "BUDGET_MINUS_25_PERCENT")[
            "total_budget_amount"
        ]
        == 1800000 * 0.75
    )
    assert apply_scenario({"ielts_overall": 7.5}, "IELTS_LOWERED")["ielts_overall"] == 6.5
    assert apply_scenario({"preferred_countries": ["DE", "NL"]}, "REMOVE_COUNTRY")[
        "preferred_countries"
    ] == ["NL"]
    assert apply_scenario({}, "DEADLINE_MISSED")["assume_deadline_missed"] is True
    assert apply_scenario({"a": 1}, "CUSTOM", {"b": 2})["b"] == 2


def test_remove_country_records_the_dropped_country() -> None:
    out = apply_scenario({"preferred_countries": ["Germany", "NL"]}, "REMOVE_COUNTRY")
    assert out["dropped_countries"] == ["Germany"]
    assert apply_scenario({"preferred_countries": []}, "REMOVE_COUNTRY").get(
        "dropped_countries"
    ) is None


def test_remove_country_honors_an_explicit_modification() -> None:
    # An explicit `modifications` entry names the country to drop and wins
    # over the preference order (API contract: REMOVE_COUNTRY + {country}).
    out = apply_scenario(
        {"preferred_countries": ["DE", "NL"]}, "REMOVE_COUNTRY", {"country": "NL"}
    )
    assert out["dropped_countries"] == ["NL"]
    assert out["preferred_countries"] == ["DE"]
    assert apply_scenario({"preferred_countries": ["DE"]}, "REMOVE_COUNTRY", {"country": ""})[
        "dropped_countries"
    ] == ["DE"]


def test_top_3_rejected_is_a_real_transformation() -> None:
    snapshot = {"cgpa": 8.0, "top_priority_program_ids": ["p1", "p2", "p3", "p4"]}
    original = dict(snapshot)
    out = apply_scenario(snapshot, "TOP_3_REJECTED")
    assert snapshot == original, "the input snapshot must never be mutated"
    assert out["rejected_program_ids"] == ["p1", "p2", "p3"]
    assert out["academic_bar_lift_cgpa"] == 0.5
    assert out["strict_prerequisites"] is True

    # Without plan context the two admissions-round effects still apply.
    flags = apply_scenario({}, "TOP_3_REJECTED")
    assert "rejected_program_ids" not in flags
    assert flags["academic_bar_lift_cgpa"] == 0.5
    assert flags["strict_prerequisites"] is True


def test_language_score_uses_ielts_bands_only() -> None:
    assert _language_score({"ielts_overall": 7.5}) == Decimal("7.5")
    assert (
        _language_score({"english_test_overall": 7.5, "english_test_type": "english_overall"})
        == Decimal("7.5")
    )
    # A TOEFL-style 96 is not an IELTS 96 — never compare it on band rules.
    assert _language_score({"english_test_overall": 96, "english_test_type": "english_overall"}) is None
    assert _language_score({"ielts_overall": 6.5, "english_test_overall": 96}) == Decimal("6.5")
    assert _language_score({}) is None


def test_academic_bar_lift_lowers_the_effective_cgpa() -> None:
    assert _facts_from({"cgpa": 8.0, "academic_bar_lift_cgpa": 0.5}).cgpa == Decimal("7.5")
    assert _facts_from({"cgpa": 8.0}).cgpa == Decimal("8.0")
    assert _facts_from({}).cgpa is None


def test_compute_delta_math() -> None:
    before = [
        {"program_id": "a", "category": "LOWER_RISK", "priority": 1},
        {"program_id": "b", "category": "TARGET", "priority": 1},
        {"program_id": "c", "category": "TARGET", "priority": 2},
    ]
    after = [
        {"program_id": "a", "category": "TARGET", "priority": 1},
        {"program_id": "c", "category": "LOWER_RISK", "priority": 1},
        {"program_id": "d", "category": "REACH", "priority": 1},
    ]
    delta = compute_delta(before, after, scenario="TEST", candidates_scored=4)
    assert [r["program_id"] for r in delta["added"]] == ["d"]
    assert [r["program_id"] for r in delta["removed"]] == ["b"]
    assert [r["program_id"] for r in delta["moved_down"]] == ["a"]
    assert [r["program_id"] for r in delta["moved_up"]] == ["c"]
    for fragment in (
        "TEST:",
        "re-scored 4 known programs",
        "1 added",
        "1 removed",
        "1 moved up",
        "1 moved down",
    ):
        assert fragment in delta["summary"]


def test_compute_delta_reports_unchanged_portfolios() -> None:
    rows = [{"program_id": "a", "category": "TARGET", "priority": 1}]
    delta = compute_delta(rows, [dict(r) for r in rows], scenario="CUSTOM", candidates_scored=1)
    assert delta["added"] == []
    assert delta["removed"] == []
    assert delta["moved_up"] == []
    assert delta["moved_down"] == []
    assert "unchanged" in delta["summary"]


# --------------------------------------------------------------- db fixtures

_PROGRAM_SPECS = [
    # (slug, country, fit score) — plan covers the first four.
    ("Alpha", "DE", Decimal("80")),
    ("Beta", "DE", Decimal("75")),
    ("Gamma", "NL", Decimal("70")),
    ("Delta", "NL", Decimal("65")),
    ("Epsilon", "FR", Decimal("60")),
    ("Zeta", "FR", Decimal("45")),
]


async def _build_strategy(db_session: Any) -> tuple[StrategyRun, list[Program]]:
    profile = await get_or_create_profile(db_session)
    profile.cgpa = Decimal("8.0")
    profile.cgpa_scale = Decimal("10")
    profile.total_budget_amount = Decimal("1800000")
    profile.budget_currency = "INR"
    profile.backlogs = 0
    english = (
        (
            await db_session.execute(
                select(StudentTestScore).where(
                    StudentTestScore.profile_id == profile.id, StudentTestScore.test_type == "english_overall"
                )
            )
        )
        .scalars()
        .first()
    )
    if english is None:
        db_session.add(
            StudentTestScore(profile_id=profile.id, test_type="english_overall", overall_score=Decimal("7.5"))
        )
    else:
        english.overall_score = Decimal("7.5")
    await db_session.commit()

    prefs = (
        (
            await db_session.execute(
                select(ProfilePreference).where(ProfilePreference.profile_id == profile.id)
            )
        )
        .scalars()
        .one()
    )
    prefs.preferred_countries = ["DE", "NL"]
    await db_session.commit()

    for code, name in (("DE", "Germany"), ("NL", "Netherlands"), ("FR", "France")):
        existing = (
            (await db_session.execute(select(Country).where(Country.code == code)))
            .scalars()
            .first()
        )
        if existing is None:
            db_session.add(Country(code=code, name=name))
            await db_session.flush()

    # Research-scoped so the candidate pool only ever holds this build's
    # programs, no matter what other tests stored for the shared profile.
    research = ResearchPlan(
        profile_id=profile.id,
        status=RunStatus.QUEUED,
        requested_goal={"goal": "simulation"},
        planned_queries=[],
        mode="demo",
    )
    db_session.add(research)
    await db_session.flush()

    deadline = (datetime.now(UTC).date() + timedelta(days=45)).isoformat()
    programs: list[Program] = []
    for slug, country, score in _PROGRAM_SPECS:
        institution = Institution(
            canonical_name=f"Sim University {slug}",
            normalized_name=f"sim university {slug}",
        )
        db_session.add(institution)
        await db_session.flush()
        program = Program(
            institution_id=institution.id,
            canonical_name=f"M.Sc. Simulation {slug}",
            normalized_name=f"m.sc. simulation {slug.lower()}",
            country_code=country,
        )
        db_session.add(program)
        await db_session.flush()
        requirements = [
            # 7.6 keeps base (CGPA 8.0) satisfied and the +0.5 bar above it.
            ("academic", "Minimum CGPA", "academic_cgpa_min", {"min": 7.6}),
            # The language bar sits AT the reported score: one band lower
            # (the IELTS_LOWERED scenario) then fails it outright instead of
            # landing inside the 0.5 "partial" band, so the counterfactual is
            # visible in the re-tier.
            ("language", "IELTS overall", "language_ielts_overall", {"min": 7.5}),
        ]
        if country in ("DE",):  # only Alpha/Beta carry the expensive tuition
            requirements.append(
                ("tuition", "Tuition ceiling", "tuition_max", {"amount": 1750000})
            )
        if slug == "Alpha":
            requirements.append(
                ("deadline", "Application deadline", "application_deadline", {"date": deadline})
            )
        for req_type, title, key, value in requirements:
            db_session.add(
                Requirement(
                    program_id=program.id,
                    requirement_type=req_type,
                    title=title,
                    normalized_key=key,
                    value=value,
                )
            )
        db_session.add(
            FitAssessment(
                profile_id=profile.id,
                research_plan_id=research.id,
                program_id=program.id,
                overall_score=score,
                scoring_version="v1",
            )
        )
        programs.append(program)

    strategy = StrategyRun(
        profile_id=profile.id,
        research_plan_id=research.id,
        status=RunStatus.SUCCEEDED,
        scoring_version="v1",
        strategy_version="v1",
        summary=f"{len(programs)} programs scored",
    )
    db_session.add(strategy)
    await db_session.flush()
    # The stored plan matches what the baseline re-score produces (all TARGET).
    for index, program in enumerate(programs[:4]):
        db_session.add(
            ApplicationPlan(
                strategy_run_id=strategy.id,
                profile_id=profile.id,
                program_id=program.id,
                category=ProgramCategory.TARGET,
                priority=index + 1,
                rationale=f"Fit score {_PROGRAM_SPECS[index][2]}",
            )
        )
    await db_session.commit()
    return strategy, programs


async def _plan_ids(db_session: Any, strategy: StrategyRun) -> list[UUID]:
    rows = (
        (
            await db_session.execute(
                select(ApplicationPlan.id).where(ApplicationPlan.strategy_run_id == strategy.id)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


# ------------------------------------------------------------------- db tests


async def test_baseline_recompute_matches_the_stored_plan(db_session: Any) -> None:
    """The re-tier uses the real helpers: unchanged profile, unchanged plan."""
    strategy, programs = await _build_strategy(db_session)
    profile = await get_or_create_profile(db_session)
    base = await _simulation_base(db_session, strategy, profile)
    modified = apply_scenario(base, "CUSTOM", {})  # no modifications

    after, scored = await recompute_portfolio(db_session, strategy, modified)
    assert scored == len(programs)
    assert {(r["program_id"], r["category"], r["priority"]) for r in after} == {
        (str(p.id), "TARGET", i + 1) for i, p in enumerate(programs[:4])
    }
    assert all(r["program_name"] for r in after)


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
async def test_every_scenario_recomputes_a_non_trivial_portfolio(
    db_session: Any, scenario: str
) -> None:
    strategy, programs = await _build_strategy(db_session)
    profile = await get_or_create_profile(db_session)
    base = await _simulation_base(db_session, strategy, profile)
    extra = {"total_budget_amount": 500000} if scenario == "CUSTOM" else None
    modified = apply_scenario(base, scenario, extra)
    assert modified != base, f"{scenario} must actually change the snapshot"

    before_ids = await _plan_ids(db_session, strategy)
    after, scored = await recompute_portfolio(db_session, strategy, modified)
    delta = compute_delta(
        before=await _before_rows(db_session, strategy),
        after=after,
        scenario=scenario,
        candidates_scored=scored,
    )

    assert after, f"{scenario} produced an empty portfolio"
    assert scored > 0
    for row in after:
        assert set(row) >= {"program_id", "program_name", "category", "priority"}
        assert row["category"] in {c.value for c in ProgramCategory}
    assert delta["summary"].strip()
    assert f"{scenario}:" in delta["summary"]
    assert (
        delta["added"] or delta["removed"] or delta["moved_up"] or delta["moved_down"]
    ), f"{scenario} should visibly change the portfolio"

    # Regeneration is pure: the stored plan is untouched.
    assert await _plan_ids(db_session, strategy) == before_ids


async def _before_rows(db_session: Any, strategy: StrategyRun) -> list[dict[str, Any]]:
    rows = (
        (
            await db_session.execute(
                select(ApplicationPlan)
                .where(ApplicationPlan.strategy_run_id == strategy.id)
                .order_by(ApplicationPlan.priority)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "program_id": str(row.program_id),
            "program_name": None,
            "category": row.category.value,
            "priority": row.priority,
        }
        for row in rows
    ]


async def test_top_3_rejected_withdraws_the_top_applications(db_session: Any) -> None:
    strategy, programs = await _build_strategy(db_session)
    profile = await get_or_create_profile(db_session)
    base = await _simulation_base(db_session, strategy, profile)
    modified = apply_scenario(base, "TOP_3_REJECTED")
    assert modified["rejected_program_ids"] == [str(p.id) for p in programs[:3]]

    after, _scored = await recompute_portfolio(db_session, strategy, modified)
    after_ids = {row["program_id"] for row in after}
    assert after, "the portfolio must survive a rejection round"
    assert after_ids.isdisjoint(modified["rejected_program_ids"])
    # ...and the hole is filled from the strategy's other known candidates.
    assert after_ids - set(modified["rejected_program_ids"])


async def test_remove_country_drops_only_that_country(db_session: Any) -> None:
    strategy, programs = await _build_strategy(db_session)
    profile = await get_or_create_profile(db_session)
    base = await _simulation_base(db_session, strategy, profile)
    assert base["preferred_countries"] == ["DE", "NL"]
    modified = apply_scenario(base, "REMOVE_COUNTRY")

    after, _scored = await recompute_portfolio(db_session, strategy, modified)
    by_id = {row["program_id"]: row for row in after}
    assert str(programs[0].id) not in by_id  # Alpha (DE) left with the country
    assert str(programs[1].id) not in by_id  # Beta (DE)
    assert str(programs[2].id) in by_id  # Gamma (NL) stays
    assert modified["dropped_countries"] == ["DE"]


async def test_remove_country_with_explicit_modification_drops_that_country(
    db_session: Any,
) -> None:
    """The API's explicit `modifications.country` beats the preference order."""
    strategy, programs = await _build_strategy(db_session)
    profile = await get_or_create_profile(db_session)
    base = await _simulation_base(db_session, strategy, profile)
    modified = apply_scenario(base, "REMOVE_COUNTRY", {"country": "NL"})
    assert modified["dropped_countries"] == ["NL"]

    after, _scored = await recompute_portfolio(db_session, strategy, modified)
    by_id = {row["program_id"]: row for row in after}
    assert str(programs[2].id) not in by_id  # Gamma (NL) removed on request
    assert str(programs[3].id) not in by_id  # Delta (NL)
    assert str(programs[0].id) in by_id  # Alpha (DE) untouched
    # Pure recompute: the stored plan is still exactly as built.
    assert len(await _plan_ids(db_session, strategy)) == 4


async def test_simulate_endpoint_returns_before_after_and_delta(db_session: Any) -> None:
    strategy, programs = await _build_strategy(db_session)
    plan_before = await _plan_ids(db_session, strategy)

    response = await simulate(strategy.id, {"scenario": "TOP_3_REJECTED"}, db_session)

    assert response["scenario"] == "TOP_3_REJECTED"
    assert UUID(response["counterfactual_run_id"])
    modified = response["modified_profile"]
    assert modified["rejected_program_ids"] == [str(p.id) for p in programs[:3]]
    assert modified["strict_prerequisites"] is True

    before = response["portfolio_before"]
    after = response["portfolio_after"]
    delta = response["delta"]
    assert len(before) == 4
    assert {k for row in before for k in row} >= {"program_id", "program_name", "category", "priority"}
    assert after and all(row["program_name"] for row in after)
    assert {"moved_up", "moved_down", "added", "removed", "summary"} == set(delta)
    assert delta["summary"].startswith("TOP_3_REJECTED:")
    removed_ids = {row["program_id"] for row in delta["removed"]}
    assert {str(p.id) for p in programs[:3]} <= removed_ids
    assert delta["removed"] and (delta["added"] or delta["moved_down"] or delta["moved_up"])

    # Nothing new was written to the plan; only the counterfactual run row.
    assert await _plan_ids(db_session, strategy) == plan_before
    run = await db_session.get(CounterfactualRun, UUID(response["counterfactual_run_id"]))
    assert run is not None
    assert run.scenario_name == "TOP_3_REJECTED"
    assert run.result["delta_summary"] == delta["summary"]
