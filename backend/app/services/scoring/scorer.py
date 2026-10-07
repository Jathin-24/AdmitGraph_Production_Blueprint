"""Fit scoring: reproducible from profile snapshot, requirement values, weights, version."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.db.models import RequirementStatus

SCORING_VERSION = "v1"

DEFAULT_WEIGHTS: dict[str, Decimal] = {
    "academic": Decimal(25),
    "prerequisites": Decimal(20),
    "language": Decimal(10),
    "financial": Decimal(15),
    "career": Decimal(15),
    "timing": Decimal(10),
    "evidence_confidence": Decimal(5),
}

STATUS_POINTS: dict[RequirementStatus, Decimal] = {
    RequirementStatus.SATISFIED: Decimal(100),
    RequirementStatus.PARTIAL: Decimal(60),
    RequirementStatus.NOT_SATISFIED: Decimal(0),
    RequirementStatus.UNKNOWN: Decimal(25),
    RequirementStatus.CONFLICTING: Decimal(40),
    RequirementStatus.NEEDS_VERIFICATION: Decimal(25),
    RequirementStatus.NOT_APPLICABLE: Decimal(100),
}


@dataclass
class DimensionInput:
    dimension: str
    statuses: list[RequirementStatus]
    evidence_count: int = 0


def dimension_score(input: DimensionInput) -> Decimal:
    if not input.statuses:
        return Decimal(0)
    points = sum(STATUS_POINTS[s] for s in input.statuses)
    return points / Decimal(len(input.statuses))


def overall_score(
    dimensions: list[DimensionInput], weights: dict[str, Decimal] | None = None
) -> tuple[Decimal, dict[str, Decimal]]:
    w = weights or DEFAULT_WEIGHTS
    total_weight = sum(w.values())
    if total_weight == 0:
        return Decimal(0), {}
    subscores: dict[str, Decimal] = {}
    acc = Decimal(0)
    for dim in dimensions:
        score = dimension_score(dim)
        subscores[dim.dimension] = score
        acc += score * w.get(dim.dimension, Decimal(0))
    return (acc / total_weight).quantize(Decimal("0.01")), subscores
