"""Portfolio selection: categories, caps, broaden decision, diversification.

The strategy portfolio is capped and diversified by code (MASTER_SPEC §19,
TEST_PLAN "Portfolio diversification" / "All top programs risky -> broaden
strategy"): never an unbounded list, never a portfolio-wide risk tilting every
slot, never a broaden without evidence that the whole top tier is risky.
"""

import uuid
from decimal import Decimal
from typing import Any

from app.db.models import ProgramCategory
from app.services.strategy.health import plan_health_score
from app.services.strategy.portfolio import (
    BROADENED_TIER_CAPS,
    MAX_PROGRAMS_PER_COUNTRY,
    MAX_PROGRAMS_PER_INSTITUTION,
    TIER_CAPS,
    Candidate,
    build_portfolio,
    categorize,
)


def test_portfolio_categories() -> None:
    assert categorize(Decimal("30")) == ProgramCategory.REACH
    assert categorize(Decimal("60")) == ProgramCategory.TARGET
    assert categorize(Decimal("80")) == ProgramCategory.LOWER_RISK
    # The 45 line: a mid-40s score (one or two dimensions still UNKNOWN) stays
    # TARGET — unknown data must not be read as "reach".
    assert categorize(Decimal("44.99")) == ProgramCategory.REACH
    assert categorize(Decimal("45")) == ProgramCategory.TARGET
    assert categorize(Decimal("69.99")) == ProgramCategory.TARGET
    assert categorize(Decimal("70")) == ProgramCategory.LOWER_RISK


def _candidate(index: int, score: int, **overrides: Any) -> Candidate:
    return Candidate(f"p{index}", Decimal(score), **overrides)


def test_portfolio_diversification_bounds() -> None:
    # Scores spanning every tier: >=70 LOWER_RISK, 45-69 TARGET, <45 REACH.
    candidates = [_candidate(i, 95 - i * 5) for i in range(20)]
    portfolio = build_portfolio(candidates)
    assert len(portfolio.tiers[ProgramCategory.REACH]) <= TIER_CAPS[ProgramCategory.REACH]
    assert len(portfolio.tiers[ProgramCategory.TARGET]) <= TIER_CAPS[ProgramCategory.TARGET]
    assert len(portfolio.tiers[ProgramCategory.LOWER_RISK]) <= TIER_CAPS[ProgramCategory.LOWER_RISK]
    # A clean (risk-free) portfolio is capped, not flooded.
    assert not portfolio.broadened
    assert portfolio.broadened_reason is None
    assert sum(len(v) for v in portfolio.tiers.values()) == sum(TIER_CAPS.values())


def test_tiers_rank_best_fit_first_within_each_cap() -> None:
    candidates = [
        _candidate(0, 91),
        _candidate(1, 90),
        _candidate(2, 89),
        _candidate(3, 50),
        _candidate(4, 40),
        _candidate(5, 30),
    ]
    portfolio = build_portfolio(candidates)
    lower_risk = portfolio.tiers[ProgramCategory.LOWER_RISK]
    # 3 LOWER_RISK candidates, cap 2: the two strongest fits survive.
    assert [c.program_id for c in lower_risk] == ["p0", "p1"]
    assert [c.program_id for c in portfolio.tiers[ProgramCategory.TARGET]] == ["p3"]
    assert [c.program_id for c in portfolio.tiers[ProgramCategory.REACH]] == ["p4", "p5"]


def test_risk_breaks_fit_ties_and_can_evict_a_riskier_program() -> None:
    candidates = [
        _candidate(0, 90, risk_count=2, top_risk_severity="HIGH"),
        _candidate(1, 90, risk_count=0, top_risk_severity=None),
    ]
    portfolio = build_portfolio(candidates)
    assert [c.program_id for c in portfolio.tiers[ProgramCategory.LOWER_RISK]] == ["p1", "p0"]


def test_broaden_only_when_the_whole_top_tier_is_high_risk() -> None:
    targets = [_candidate(3 + i, 65 - i) for i in range(6)]  # 65..60, TARGET
    # One MEDIUM top-tier candidate: the tier is not uniformly risky, so the
    # standard caps stand.
    mixed = [
        _candidate(0, 95, top_risk_severity="CRITICAL"),
        _candidate(1, 92, top_risk_severity="HIGH"),
        _candidate(2, 91, top_risk_severity="MEDIUM"),
        _candidate(9, 90, top_risk_severity="HIGH"),
    ]
    not_broadened = build_portfolio(mixed + targets)
    assert not not_broadened.broadened
    assert not_broadened.broadened_reason is None
    assert len(not_broadened.tiers[ProgramCategory.LOWER_RISK]) == TIER_CAPS[
        ProgramCategory.LOWER_RISK
    ]
    assert len(not_broadened.tiers[ProgramCategory.TARGET]) == TIER_CAPS[ProgramCategory.TARGET]

    # Every top-tier candidate carries an OPEN HIGH/CRITICAL risk -> caps relax.
    all_risky = [
        _candidate(0, 95, top_risk_severity="CRITICAL"),
        _candidate(1, 92, top_risk_severity="HIGH"),
        _candidate(2, 71, top_risk_severity="HIGH"),
    ]
    broadened = build_portfolio(all_risky + targets)
    assert broadened.broadened
    assert "HIGH or CRITICAL" in (broadened.broadened_reason or "")
    assert (
        len(broadened.tiers[ProgramCategory.LOWER_RISK])
        == BROADENED_TIER_CAPS[ProgramCategory.LOWER_RISK]
    )
    assert (
        len(broadened.tiers[ProgramCategory.TARGET]) == BROADENED_TIER_CAPS[ProgramCategory.TARGET]
    )
    # Nothing risky in the top tier -> no broaden at all.
    assert not build_portfolio([_candidate(7, 80), _candidate(8, 75)]).broadened


def test_institution_cap_limits_programs_per_institution() -> None:
    same = str(uuid.uuid4())
    other = str(uuid.uuid4())
    candidates = [
        _candidate(0, 95, institution_id=same),
        _candidate(1, 94, institution_id=same),
        _candidate(2, 93, institution_id=same),
        _candidate(3, 80, institution_id=other),
        _candidate(4, 79, institution_id=other),
    ]
    portfolio = build_portfolio(candidates)
    selected = [c for tier in portfolio.tiers.values() for c in tier]
    assert sum(1 for c in selected if c.institution_id == same) == MAX_PROGRAMS_PER_INSTITUTION
    # The strongest two of that institution keep their slots.
    assert {c.program_id for c in selected if c.institution_id == same} == {"p0", "p1"}
    # Unknown institutions are never grouped (no data must not evict anyone):
    # four TARGET-tier programs without an institution all keep their slot.
    unknown = build_portfolio(
        [_candidate(i, 65 - i, institution_id=None) for i in range(MAX_PROGRAMS_PER_INSTITUTION + 2)]
    )
    assert len([c for t in unknown.tiers.values() for c in t]) == MAX_PROGRAMS_PER_INSTITUTION + 2


def test_country_cap_limits_programs_per_country() -> None:
    country = "DE"
    # 4 TARGET + 2 REACH programs from one country: the tier caps alone would
    # admit all six, the country cap trims them to MAX_PROGRAMS_PER_COUNTRY.
    candidates = [
        _candidate(0, 65, country_code=country),
        _candidate(1, 64, country_code=country),
        _candidate(2, 63, country_code=country),
        _candidate(3, 62, country_code=country),
        _candidate(4, 40, country_code=country),
        _candidate(5, 39, country_code=country),
    ]
    portfolio = build_portfolio(candidates)
    selected = [c for tier in portfolio.tiers.values() for c in tier]
    assert len(selected) == MAX_PROGRAMS_PER_COUNTRY
    # Visited best-tier-first: the four TARGET programs keep the country's slots.
    assert [c.program_id for c in selected] == ["p0", "p1", "p2", "p3"]
    assert all(c.country_code == country for c in selected)


def test_health_drops_with_blockers() -> None:
    healthy = plan_health_score(
        blocker_count=0,
        unresolved_conflicts=0,
        stale_evidence_count=0,
        nearest_deadline=None,
        financial_feasible=True,
        documents_ready=True,
    )
    unhealthy = plan_health_score(
        blocker_count=2,
        unresolved_conflicts=2,
        stale_evidence_count=3,
        nearest_deadline=None,
        financial_feasible=False,
        documents_ready=False,
    )
    assert healthy > unhealthy >= Decimal(0)
