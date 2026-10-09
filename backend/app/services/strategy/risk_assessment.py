"""Pure risk-to-evidence mapping for the strategy pipeline.

Deterministic rules that give each engine-produced risk its confidence level
and link it back to the exact evidence rows it derives from — no session, no
I/O — moved out of ``app.services.strategy.persist`` (P2-22).
"""

from __future__ import annotations

import uuid

from app.db.models import ConfidenceLevel
from app.services.risk.engine import RiskAssessment


def risk_confidence(risk: RiskAssessment) -> ConfidenceLevel:
    """How sure we are the risk is real (MASTER_SPEC §8 "confidence").

    HIGH — the risk reads a concrete stored fact: a requirement evaluated as
    NOT_SATISFIED/CONFLICTING, a deadline date already in the past, an expired
    test date, core document rows that are not DONE, or conflicting/stale
    evidence rows.
    MEDIUM — derived comparisons (budget vs tuition) and absence-of-data risks
    (unknown requirement, no career evidence): real, but about what we do not
    know rather than what we observed.
    """
    if risk.risk_type in ("SOURCE_CONFLICT", "INFORMATION_FRESHNESS", "DOCUMENT"):
        return ConfidenceLevel.HIGH
    if risk.risk_type == "DEADLINE" and risk.title == "Deadline has passed":
        return ConfidenceLevel.HIGH
    if risk.risk_type == "TEST" and risk.title == "Language test expired":
        return ConfidenceLevel.HIGH
    if risk.title.startswith(
        ("Requirement not met:", "Optional requirement not met:", "Conflicting information for")
    ):
        return ConfidenceLevel.HIGH
    return ConfidenceLevel.MEDIUM


def evidence_for_risk(
    risk: RiskAssessment,
    *,
    key_evidence_ids: dict[str, list[uuid.UUID]],
    conflict_evidence_ids: list[uuid.UUID],
    stale_evidence_ids: list[uuid.UUID],
    deadline_evidence_ids: list[uuid.UUID],
    tuition_evidence_ids: list[uuid.UUID],
    language_test_evidence_ids: list[uuid.UUID],
) -> list[uuid.UUID]:
    """Map a deterministic risk back to the evidence it derives from (bounded)."""
    ids: set[uuid.UUID] = set()
    title = risk.title
    if ": " in title and title.split(": ", 1)[0] in (
        "Requirement not met",
        "Optional requirement not met",
        "Requirement unknown",
        "Requirement unverified",
        "Conflicting information for",
    ):
        key = title.split(": ", 1)[1]
        ids.update(key_evidence_ids.get(key, []))
    if risk.risk_type == "SOURCE_CONFLICT":
        ids.update(conflict_evidence_ids)
    if title == "Stale evidence":
        ids.update(stale_evidence_ids)
    if risk.risk_type == "DEADLINE":
        ids.update(deadline_evidence_ids)
    if risk.risk_type == "FINANCIAL" or title.startswith("Budget below"):
        ids.update(tuition_evidence_ids)
    if risk.risk_type == "TEST":
        ids.update(language_test_evidence_ids)
    return sorted(ids)[:10]
