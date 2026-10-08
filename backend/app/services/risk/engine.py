"""Deterministic risk engine. Rules are code/config, never LLM-only."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import UUID

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

# An eligibility entry: (requirement key, status, mandatory[, requirement id]).
# The optional 4th element lets callers link the risk back to the exact
# Requirement row that raised it; 3-element tuples stay valid.
EligibilityEntry = tuple[str, RequirementStatus, bool] | tuple[str, RequirementStatus, bool, UUID]


@dataclass
class RiskAssessment:
    risk_type: str
    severity: RiskSeverity
    title: str
    reason: str
    recommended_action: str
    # Provenance of the risk (both optional: global risks have neither).
    requirement_id: UUID | None = None
    program_id: UUID | None = None


@dataclass
class RiskContext:
    eligibility: list[EligibilityEntry] = field(default_factory=list)
    # tuple is (requirement key, status, mandatory[, requirement id])
    budget: Decimal | None = None
    estimated_cost: Decimal | None = None
    next_deadline: date | None = None
    documents_ready: bool = True
    # Core document types (e.g. "transcript") that are not ready yet; empty
    # means every core document is DONE (or nothing was tracked).
    missing_documents: list[str] = field(default_factory=list)
    stale_evidence_count: int = 0
    conflicts: list[str] = field(default_factory=list)
    # (program id, program name) pairs for portfolio programs without any
    # claim_type="career" evidence; empty when every program has some.
    programs_without_career_evidence: list[tuple[UUID, str]] = field(default_factory=list)
    # None = nothing known about the language test (rule stays silent);
    # True/False = a test is (not) on record, plus its expiry when known.
    language_test_present: bool | None = None
    language_test_expiry: date | None = None


def assess(ctx: RiskContext, today: date | None = None) -> list[RiskAssessment]:
    today = today or date.today()
    risks: list[RiskAssessment] = []

    for entry in ctx.eligibility:
        # Index-based unpack so 3- and 4-element entries both work under mypy.
        parts: tuple[object, ...] = entry
        key = str(parts[0])
        status = RequirementStatus(str(parts[1]))
        mandatory = bool(parts[2])
        requirement_id = parts[3] if len(parts) > 3 else None
        rid = requirement_id if isinstance(requirement_id, UUID) else None

        if mandatory and status == RequirementStatus.NOT_SATISFIED:
            risk_type = _type_for_key(key)
            severity = (
                RiskSeverity.CRITICAL
                if risk_type in ("LANGUAGE", "PREREQUISITE") or key in ("language", "academic")
                else RiskSeverity.HIGH
            )
            risks.append(
                RiskAssessment(
                    risk_type=risk_type,
                    severity=severity,
                    title=f"Requirement not met: {key}",
                    reason=f"Mandatory requirement '{key}' is not satisfied by the current profile.",
                    recommended_action=(
                        f"Address '{key}' before applying or choose a program without this requirement."
                    ),
                    requirement_id=rid,
                )
            )
        elif not mandatory and status == RequirementStatus.NOT_SATISFIED:
            # Optional but unmet: typed by requirement key, one step below the
            # mandatory severity ladder (HIGH for prerequisite/language/
            # eligibility keys, MEDIUM for the rest).
            risk_type = _type_for_key(key)
            severity = (
                RiskSeverity.HIGH
                if risk_type in ("PREREQUISITE", "LANGUAGE", "ELIGIBILITY")
                else RiskSeverity.MEDIUM
            )
            risks.append(
                RiskAssessment(
                    risk_type=risk_type,
                    severity=severity,
                    title=f"Optional requirement not met: {key}",
                    reason=f"Optional requirement '{key}' is not satisfied by the current profile.",
                    recommended_action=(
                        f"Check whether '{key}' applies to you; if it does, address it before applying."
                    ),
                    requirement_id=rid,
                )
            )
        elif mandatory and status == RequirementStatus.UNKNOWN:
            # Mandatory + UNKNOWN: a typed risk, never an invented score.
            # LANGUAGE for language keys, PREREQUISITE for prerequisite keys,
            # ELIGIBILITY otherwise.
            base = _type_for_key(key)
            risk_type = base if base in ("LANGUAGE", "PREREQUISITE") else "ELIGIBILITY"
            risks.append(
                RiskAssessment(
                    risk_type=risk_type,
                    severity=RiskSeverity.HIGH,
                    title=f"Requirement unknown: {key}",
                    reason=(
                        f"Mandatory requirement '{key}' cannot be evaluated: no matching "
                        "profile fact or evidence exists yet."
                    ),
                    recommended_action=(
                        f"provide or verify '{key}' (upload the document or add the score) "
                        "so eligibility can be evaluated."
                    ),
                    requirement_id=rid,
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
                    requirement_id=rid,
                )
            )
        elif status == RequirementStatus.CONFLICTING:
            risks.append(
                RiskAssessment(
                    risk_type="SOURCE_CONFLICT",
                    severity=RiskSeverity.HIGH,
                    title=f"Conflicting information for {key}",
                    reason=f"Sources disagree about '{key}'.",
                    recommended_action=(
                        "Check the official source directly; do not rely on a single secondary claim."
                    ),
                    requirement_id=rid,
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

    # A core document that is not READY blocks the application package
    # (MASTER_SPEC §17): one risk naming every missing core type.
    if ctx.missing_documents:
        missing = ", ".join(ctx.missing_documents)
        risks.append(
            RiskAssessment(
                risk_type="DOCUMENT",
                severity=RiskSeverity.HIGH,
                title="Core documents not ready",
                reason=f"Not ready yet: {missing}.",
                recommended_action="Upload or obtain the missing core documents before applying.",
            )
        )

    # Language test validity (TEST): missing on record, expired, or on record
    # without an expiry date (validity cannot be confirmed -> verify).
    if ctx.language_test_present is False:
        risks.append(
            RiskAssessment(
                risk_type="TEST",
                severity=RiskSeverity.HIGH,
                title="Language test missing",
                reason="No language test score is on record.",
                recommended_action="provide a language test result (IELTS/TOEFL/PTE/...).",
            )
        )
    elif ctx.language_test_present and ctx.language_test_expiry is None:
        risks.append(
            RiskAssessment(
                risk_type="TEST",
                severity=RiskSeverity.MEDIUM,
                title="Language test expiry unknown",
                reason="A language test is on record but carries no expiry date.",
                recommended_action="verify the test validity window with the issuing body.",
            )
        )
    elif (
        ctx.language_test_present
        and ctx.language_test_expiry is not None
        and ctx.language_test_expiry < today
    ):
        risks.append(
            RiskAssessment(
                risk_type="TEST",
                severity=RiskSeverity.HIGH,
                title="Language test expired",
                reason=f"The recorded language test expired on {ctx.language_test_expiry}.",
                recommended_action="retake the language test before applying.",
            )
        )

    # Career evidence is program-scoped: one risk per portfolio program that
    # has none, so the card can be acted on for that program specifically.
    for program_id, program_name in ctx.programs_without_career_evidence:
        risks.append(
            RiskAssessment(
                risk_type="CAREER",
                severity=RiskSeverity.MEDIUM,
                title=f"No career evidence for {program_name}",
                reason=(
                    f"No claim_type='career' evidence is stored for {program_name}; "
                    "career alignment is unknown."
                ),
                recommended_action=(
                    "Review the program's career outcomes manually or re-run research "
                    "to collect career claims."
                ),
                program_id=program_id,
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
