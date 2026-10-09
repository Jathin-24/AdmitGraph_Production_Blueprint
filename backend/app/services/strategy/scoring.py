"""Pure fit-score derivations shared by the strategy pipeline and simulator.

Everything here is deterministic over already-loaded rows — no session, no
I/O — moved out of ``app.services.strategy.persist`` (P2-22) so the scoring
rules read as one module. persist re-exports the names its callers import.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.db.models import (
    ConfidenceLevel,
    Evidence,
    EvidenceStatus,
    FitAssessment,
    Program,
    Requirement,
    RequirementStatus,
)
from app.services.matching.evaluator import ProfileFacts

# Dimensions whose status is derived from stored evidence (shared with the
# counterfactual simulator so a recompute mirrors the stored strategy).
EVIDENCE_DIMENSIONS = ("career", "timing", "evidence_confidence")
TIMING_CLAIM_TYPES = ("policy", "deadline")

# Tuition cost bands, per currency (MASTER_SPEC card "estimated cost band").
# No FX conversion is invented: below the first threshold is LOW, below the
# second MEDIUM, otherwise HIGH. A currency outside this table (or a missing
# amount) has an UNKNOWN band -> None, never a guessed one.
TUITION_BAND_THRESHOLDS: dict[str, tuple[Decimal, Decimal]] = {
    "EUR": (Decimal("15000"), Decimal("35000")),
    "GBP": (Decimal("15000"), Decimal("35000")),
    "USD": (Decimal("15000"), Decimal("35000")),
    "AUD": (Decimal("15000"), Decimal("35000")),
    "CAD": (Decimal("15000"), Decimal("35000")),
    "CHF": (Decimal("15000"), Decimal("35000")),
    "INR": (Decimal("500000"), Decimal("1500000")),
}


def evaluation_value(req: Requirement) -> dict[str, Any]:
    """Requirement value handed to the evaluator.

    An application window {start, end} is evaluated on its END date — copied
    into a fresh dict, never written back, so the stored JSONB keeps exactly
    what was extracted.
    """
    value = req.value or {}
    if req.normalized_key.strip().lower() == "application_deadline" and "date" not in value:
        if value.get("end"):
            return {**value, "date": value["end"]}
    return dict(value)


def facts_payload(facts: ProfileFacts) -> dict[str, Any]:
    """The profile facts an evaluation ran against, JSONB-safe.

    Decimals/dates become strings so the row stores exactly what was compared,
    ready for a later recalculation.
    """

    def _num(value: Decimal | None) -> str | None:
        return str(value) if value is not None else None

    return {
        "cgpa": _num(facts.cgpa),
        "cgpa_scale": _num(facts.cgpa_scale),
        "percentage": _num(facts.percentage),
        "ielts_overall": _num(facts.ielts_overall),
        "english_test_type": facts.english_test_type,
        "language_score": _num(facts.language_score),
        "language_expiry_date": (
            facts.language_expiry_date.isoformat() if facts.language_expiry_date else None
        ),
        "matched_credits": _num(facts.matched_credits),
        "backlogs": facts.backlogs,
        "total_budget_amount": _num(facts.total_budget_amount),
        "budget_currency": facts.budget_currency,
        "graduation_year": facts.graduation_year,
        "subjects": sorted(facts.subjects),
    }


def eligibility_confidence(status: RequirementStatus, has_evidence: bool) -> ConfidenceLevel:
    """Confidence of one eligibility row (deterministic, documented):
    LOW — sources disagree (CONFLICTING) or there is nothing to compare (UNKNOWN);
    MEDIUM — a value exists but is unconfirmed (NEEDS_VERIFICATION), or the
    result derives only from profile facts with no stored evidence behind it;
    HIGH — a definite evaluation backed by stored evidence rows.
    """
    if status in (RequirementStatus.CONFLICTING, RequirementStatus.UNKNOWN):
        return ConfidenceLevel.LOW
    if status is RequirementStatus.NEEDS_VERIFICATION:
        return ConfidenceLevel.MEDIUM
    return ConfidenceLevel.HIGH if has_evidence else ConfidenceLevel.MEDIUM


def evidence_status(
    rows: list[Evidence], *, confidence_rule: bool, now: datetime
) -> RequirementStatus:
    """Status one dimension derives from its evidence rows (documented rules):
    no rows -> UNKNOWN; any CONFLICTING row -> CONFLICTING; with the
    confidence rule, SATISFIED only when every row is CURRENT and still inside
    its freshness window, else NEEDS_VERIFICATION; without it, rows present and
    clean -> NEEDS_VERIFICATION (a signal we hold, and should re-check).
    """
    if not rows:
        return RequirementStatus.UNKNOWN
    if any(row.status == EvidenceStatus.CONFLICTING for row in rows):
        return RequirementStatus.CONFLICTING
    if confidence_rule:
        all_current_and_fresh = all(
            row.status == EvidenceStatus.CURRENT
            and (row.freshness_deadline is None or row.freshness_deadline >= now)
            for row in rows
        )
        return (
            RequirementStatus.SATISFIED if all_current_and_fresh else RequirementStatus.NEEDS_VERIFICATION
        )
    return RequirementStatus.NEEDS_VERIFICATION


def evidence_dimension_statuses(
    rows: list[Evidence], *, now: datetime | None = None
) -> dict[str, RequirementStatus]:
    """Career / timing / evidence-confidence statuses for one program.

    Derived ONLY from stored evidence rows (claim_type "career" feeds career,
    "policy"/"deadline" feed timing, everything feeds evidence confidence) —
    shared by the pipeline and the simulator so a recompute mirrors the stored
    strategy. Missing evidence stays UNKNOWN: absence of data is not a verdict.
    """
    moment = now or datetime.now(UTC)
    career = [row for row in rows if row.claim_type == "career"]
    timing = [row for row in rows if row.claim_type in TIMING_CLAIM_TYPES]
    return {
        "career": evidence_status(career, confidence_rule=False, now=moment),
        "timing": evidence_status(timing, confidence_rule=False, now=moment),
        "evidence_confidence": evidence_status(rows, confidence_rule=True, now=moment),
    }


def evidence_links_by_dimension(rows: list[Evidence]) -> dict[str, list[uuid.UUID]]:
    """fit_dimension_evidence rows for one program: which evidence backs which
    dimension (an empty dimension gets no row rather than a fabricated link)."""
    links: dict[str, set[uuid.UUID]] = {dim: set() for dim in EVIDENCE_DIMENSIONS}
    for row in rows:
        links["evidence_confidence"].add(row.id)
        if row.claim_type == "career":
            links["career"].add(row.id)
        elif row.claim_type in TIMING_CLAIM_TYPES:
            links["timing"].add(row.id)
    return {dim: sorted(ids) for dim, ids in links.items() if ids}


def tuition_cost_band(amount: Decimal | None, currency: str | None) -> str | None:
    """In-currency tuition band: LOW / MEDIUM / HIGH, or None when the amount
    or the currency is unknown (UNKNOWN is valid — never a guessed band)."""
    if amount is None or not currency:
        return None
    thresholds = TUITION_BAND_THRESHOLDS.get(currency.strip().upper())
    if thresholds is None:
        return None
    low, medium = thresholds
    if amount < low:
        return "LOW"
    if amount < medium:
        return "MEDIUM"
    return "HIGH"


def estimated_cost(program: Program | None) -> dict[str, Any]:
    """Stored tuition with its band (JSONB). Empty when tuition was never
    extracted — no invented currency, amount, or band."""
    if program is None or program.tuition_amount is None:
        return {}
    return {
        "currency": program.tuition_currency,
        "amount": float(program.tuition_amount),
        "band": tuition_cost_band(program.tuition_amount, program.tuition_currency),
    }


def top_reasons(fit: FitAssessment | None) -> list[str]:
    """The two strongest dimensions behind this card ("top 2 reasons",
    FRONTEND_SPEC strategy card). Only stored subscores are quoted; a row
    without subscores carries no reasons rather than fabricated ones."""
    if fit is None:
        return []
    parts = (
        ("Academic", fit.academic_score),
        ("Prerequisites", fit.prerequisite_score),
        ("Language", fit.language_score),
        ("Financial", fit.financial_score),
        ("Career", fit.career_score),
        ("Timing", fit.timing_score),
        ("Evidence confidence", fit.evidence_confidence_score),
    )
    scored: list[tuple[str, Decimal]] = []
    for label, value in parts:
        if value is not None:
            scored.append((label, value))
    scored.sort(key=lambda item: item[1], reverse=True)
    return [
        f"{label} {value.quantize(Decimal('1'), rounding=ROUND_HALF_UP)}/100"
        for label, value in scored[:2]
    ]
