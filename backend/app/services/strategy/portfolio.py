"""Deterministic strategy portfolio generation. Robustness over count."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.db.models import ProgramCategory

# Category bounds on the 0-100 fit score: REACH < 45, TARGET 45-69.99,
# LOWER_RISK >= 70. The 45 line keeps a mid-40s score (typical when one or two
# dimensions are still UNKNOWN) in TARGET: unknown data must not be read as
# "reach" — that would demote programs purely for evidence we do not have yet.
REACH_UPPER = Decimal("45")
LOWER_RISK_MIN = Decimal("70")

# Standard tier caps: a portfolio stays small and reviewable.
TIER_CAPS: dict[ProgramCategory, int] = {
    ProgramCategory.REACH: 2,
    ProgramCategory.TARGET: 4,
    ProgramCategory.LOWER_RISK: 2,
}

# Broadening (TEST_PLAN "All top programs risky -> broaden strategy"): when
# every candidate eligible for the top tier (LOWER_RISK) carries an OPEN risk
# of severity >= BROADEN_MIN_SEVERITY, the caps relax to admit the next-best
# candidates — up to these documented ceilings (3 + 6 + 3 = 12 programs, the
# broadened portfolio's hard cap).
BROADENED_TIER_CAPS: dict[ProgramCategory, int] = {
    ProgramCategory.REACH: 3,
    ProgramCategory.TARGET: 6,
    ProgramCategory.LOWER_RISK: 3,
}
BROADEN_MIN_SEVERITY = ("CRITICAL", "HIGH")

# Diversification (TEST_PLAN "Portfolio diversification"): the FINAL portfolio
# holds at most MAX_PROGRAMS_PER_INSTITUTION programs from one institution and
# at most MAX_PROGRAMS_PER_COUNTRY programs from one country. Applied after
# tiering; candidates whose institution/country is unknown are never grouped
# (no data must not silently evict a known program).
MAX_PROGRAMS_PER_INSTITUTION = 2
MAX_PROGRAMS_PER_COUNTRY = 4


@dataclass
class Candidate:
    program_id: str
    fit_score: Decimal
    risk_count: int = 0
    # Worst severity among this program's OPEN risks ("CRITICAL"/"HIGH"/...),
    # None when the program carries no open risk.
    top_risk_severity: str | None = None
    # Grouping keys for diversification; None = unknown, never grouped.
    institution_id: str | None = None
    country_code: str | None = None


@dataclass
class PortfolioResult:
    """Tier selection outcome: the capped tiers plus the broaden decision."""

    tiers: dict[ProgramCategory, list[Candidate]]
    broadened: bool = False
    broadened_reason: str | None = None


def categorize(score: Decimal) -> ProgramCategory:
    if score < REACH_UPPER:
        return ProgramCategory.REACH
    if score < LOWER_RISK_MIN:
        return ProgramCategory.TARGET
    return ProgramCategory.LOWER_RISK


def build_portfolio(candidates: list[Candidate]) -> PortfolioResult:
    by_category: dict[ProgramCategory, list[Candidate]] = {
        ProgramCategory.REACH: [],
        ProgramCategory.TARGET: [],
        ProgramCategory.LOWER_RISK: [],
    }
    # Sort each category by fit desc, risk asc.
    for cand in sorted(candidates, key=lambda c: (c.fit_score, -c.risk_count), reverse=True):
        by_category[categorize(cand.fit_score)].append(cand)

    broadened, reason = _broaden_decision(by_category[ProgramCategory.LOWER_RISK])
    caps = BROADENED_TIER_CAPS if broadened else TIER_CAPS
    selected: dict[ProgramCategory, list[Candidate]] = {
        tier: items[: caps[tier]] for tier, items in by_category.items()
    }
    return PortfolioResult(
        tiers=_diversify(selected),
        broadened=broadened,
        broadened_reason=reason,
    )


def _broaden_decision(top_tier: list[Candidate]) -> tuple[bool, str | None]:
    """Broaden only when the whole top tier is high-risk (and it is non-empty).

    An empty top tier is not "everything is risky" — it is simply no data.
    """
    if not top_tier:
        return False, None
    risky = [c for c in top_tier if c.top_risk_severity in BROADEN_MIN_SEVERITY]
    if len(risky) != len(top_tier):
        return False, None
    return True, (
        f"All {len(top_tier)} candidate(s) eligible for the top tier carry an open risk of "
        "severity HIGH or CRITICAL, so the tier caps were relaxed to admit the next-best "
        f"candidates (up to {sum(BROADENED_TIER_CAPS.values())} programs)."
    )


def _diversify(
    selected: dict[ProgramCategory, list[Candidate]],
) -> dict[ProgramCategory, list[Candidate]]:
    """Apply the institution/country caps to the selected portfolio.

    Tiers are visited best-first (LOWER_RISK -> TARGET -> REACH) so the
    strongest fits keep their slots when a cap must evict somebody; within a
    tier the existing fit-desc/risk-asc order stands. Priorities are re-numbered
    by the caller from these compacted lists.
    """
    tier_order = (ProgramCategory.LOWER_RISK, ProgramCategory.TARGET, ProgramCategory.REACH)
    institution_counts: dict[str, int] = {}
    country_counts: dict[str, int] = {}
    out: dict[ProgramCategory, list[Candidate]] = {tier: [] for tier in selected}
    for tier in tier_order:
        for cand in selected.get(tier, []):
            inst_key = cand.institution_id
            country_key = cand.country_code
            inst_ok = inst_key is None or institution_counts.get(inst_key, 0) < MAX_PROGRAMS_PER_INSTITUTION
            country_ok = country_key is None or country_counts.get(country_key, 0) < MAX_PROGRAMS_PER_COUNTRY
            if not (inst_ok and country_ok):
                continue
            if inst_key is not None:
                institution_counts[inst_key] = institution_counts.get(inst_key, 0) + 1
            if country_key is not None:
                country_counts[country_key] = country_counts.get(country_key, 0) + 1
            out.setdefault(tier, []).append(cand)
    return out
