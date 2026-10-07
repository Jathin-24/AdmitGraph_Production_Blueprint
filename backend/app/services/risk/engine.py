"""Deterministic risk engine. Rules are code/config, never LLM-only."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.db.models import RequirementStatus, RiskSeverity

RISK_TYPES = {
    "ELIGIBILITY",
    "PREREQUISITE",
    "LANGUAGE",
    "TEST",
    "DEADLINE",
    "FINANCIAL",
    "DOCUMENT",
    "VISA_COMPLIANCE",
    "CAREER",
    "INFORMATION_FRESHNESS",
    "SOURCE_CONFLICT",
    "DATA_QUALITY",
}


@dataclass
class RiskAssessment:
    risk_type: str
    severity: RiskSeverity
    title: str
    reason: str
    recommended_action: str


@dataclass
class RiskContext:
    eligibility: list[tuple[str, RequirementStatus, bool]] = field(default_factory=list)  # (key, status, mandatory)
    budget: Decimal | None = None
    estimated_cost: Decimal | None = None
    next_deadline: date | None = None
    documents_ready: bool = True
    stale_evidence_count: int = 0
    conflicts: list[str] = field(default_factory=list)


def assess(ctx: RiskContext, today: date | None = None) -> list[RiskAssessment]:
    today = today or date.today()
    risks: list[RiskAssessment] = []

    for key, status, mandatory in ctx.eligibility:
        if mandatory and status == RequirementStatus.NOT_SATISFIED:
            severity = RiskSeverity.CRITICAL if key in ("language", "academic") else RiskSeverity.HIGH
            risks.append(
                RiskAssessment(
                    risk_type=_type_for_key(key),
                    severity=severity,
                    title=f"Requirement not met: {key}",
                    reason=f"Mandatory requirement '{key}' is not satisfied by the current profile.",
                    recommended_action=f"Address '{key}' before applying or choose a program without this requirement.",
                )
            )
        elif status in (RequirementStatus.UNKNOWN, RequirementStatus.NEEDS_VERIFICATION):
            risks.append(
                RiskAssessment(
                    risk_type="DATA_QUALITY",
                    severity=RiskSeverity.MEDIUM,
                    title=f"Requirement unverified: {key}",
                    reason=f"Current evidence for '{key}' is insufficient to confirm eligibility.",
                    recommended_action="Verify this requirement with the official program page.",
                )
            )
        elif status == RequirementStatus.CONFLICTING:
            risks.append(
                RiskAssessment(
                    risk_type="SOURCE_CONFLICT",
                    severity=RiskSeverity.HIGH,
                    title=f"Conflicting information for {key}",
                    reason=f"Sources disagree about '{key}'.",
                    recommended_action="Check the official source directly; do not rely on a single secondary claim.",
                )
            )

    if ctx.next_deadline is not None:
        if ctx.next_deadline < today:
            risks.append(
                RiskAssessment(
                    risk_type="DEADLINE",
                    severity=RiskSeverity.CRITICAL,
                    title="Deadline has passed",
                    reason=f"The next application deadline ({ctx.next_deadline}) is in the past.",
                    recommended_action="Target a later intake or a program with a live deadline.",
                )
            )
        elif (ctx.next_deadline - today).days < 14 and not ctx.documents_ready:
            risks.append(
                RiskAssessment(
                    risk_type="DEADLINE",
                    severity=RiskSeverity.HIGH,
                    title="Deadline soon and documents incomplete",
                    reason=f"Deadline in {(ctx.next_deadline - today).days} days with documents not ready.",
                    recommended_action="Prepare documents now or defer to the next intake.",
                )
            )

    if ctx.budget is not None and ctx.estimated_cost is not None and ctx.budget < ctx.estimated_cost:
        ratio = ctx.budget / ctx.estimated_cost if ctx.estimated_cost > 0 else Decimal(0)
        severity = RiskSeverity.HIGH if ratio < Decimal("0.8") else RiskSeverity.MEDIUM
        risks.append(
            RiskAssessment(
                risk_type="FINANCIAL",
                severity=severity,
                title="Budget below estimated cost",
                reason=f"Budget {ctx.budget} is below estimated cost {ctx.estimated_cost}.",
                recommended_action="Look for scholarships, lower-tuition programs, or part-time options.",
            )
        )

    if ctx.stale_evidence_count > 0:
        risks.append(
            RiskAssessment(
                risk_type="INFORMATION_FRESHNESS",
                severity=RiskSeverity.MEDIUM,
                title="Stale evidence",
                reason=f"{ctx.stale_evidence_count} claim(s) are past their freshness window.",
                recommended_action="Re-check these claims against the official source.",
            )
        )

    for conflict in ctx.conflicts:
        risks.append(
            RiskAssessment(
                risk_type="SOURCE_CONFLICT",
                severity=RiskSeverity.HIGH,
                title="Unresolved source conflict",
                reason=conflict,
                recommended_action="Resolve with the official source before deciding.",
            )
        )

    return risks


def _type_for_key(key: str) -> str:
    k = key.lower()
    if "language" in k or "ielts" in k or "english" in k:
        return "LANGUAGE"
    if "prereq" in k or "subject" in k or "credit" in k:
        return "PREREQUISITE"
    if "deadline" in k:
        return "DEADLINE"
    if "budget" in k or "tuition" in k or "cost" in k:
        return "FINANCIAL"
    if "visa" in k:
        return "VISA_COMPLIANCE"
    return "ELIGIBILITY"
