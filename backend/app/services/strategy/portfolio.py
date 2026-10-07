"""Deterministic strategy portfolio generation. Robustness over count."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.db.models import ProgramCategory

REACH_MIN = Decimal("0")
REACH_MAX = Decimal("49.99")
LOWER_RISK_MIN = Decimal("70")


@dataclass
class Candidate:
    program_id: str
    fit_score: Decimal
    risk_count: int = 0


def categorize(score: Decimal) -> ProgramCategory:
    if score < Decimal("50"):
        return ProgramCategory.REACH
    if score < LOWER_RISK_MIN:
        return ProgramCategory.TARGET
    return ProgramCategory.LOWER_RISK


def build_portfolio(candidates: list[Candidate]) -> dict[ProgramCategory, list[Candidate]]:
    by_category: dict[ProgramCategory, list[Candidate]] = {
        ProgramCategory.REACH: [],
        ProgramCategory.TARGET: [],
        ProgramCategory.LOWER_RISK: [],
    }
    # Sort each category by fit desc, risk asc.
    for cand in sorted(candidates, key=lambda c: (c.fit_score, -c.risk_count), reverse=True):
        by_category[categorize(cand.fit_score)].append(cand)

    portfolio: dict[ProgramCategory, list[Candidate]] = {
        ProgramCategory.REACH: by_category[ProgramCategory.REACH][:2],
        ProgramCategory.TARGET: by_category[ProgramCategory.TARGET][:4],
        ProgramCategory.LOWER_RISK: by_category[ProgramCategory.LOWER_RISK][:2],
    }
    return portfolio
