"""Deterministic requirement matching. No LLM overrides allowed."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from app.db.models import RequirementStatus


@dataclass
class ProfileFacts:
    cgpa: Decimal | None = None
    cgpa_scale: Decimal | None = None
    percentage: Decimal | None = None
    ielts_overall: Decimal | None = None
    english_test_type: str | None = None
    backlogs: int = 0
    total_budget_amount: Decimal | None = None
    budget_currency: str | None = None
    graduation_year: int | None = None
    months_experience: int = 0
    subjects: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        # Numeric facts may arrive as float/int (API payloads, in-session ORM
        # assignments); all comparisons below are Decimal math.
        self.cgpa = _num(self.cgpa)
        self.cgpa_scale = _num(self.cgpa_scale)
        self.percentage = _num(self.percentage)
        self.ielts_overall = _num(self.ielts_overall)
        self.total_budget_amount = _num(self.total_budget_amount)


def _num(v: Any) -> Decimal | None:
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except (TypeError, ValueError, ArithmeticError):
        return None


def _normalized(text: str) -> str:
    return text.strip().lower().replace(" ", "_")


def _threshold_status(got: Decimal, want: Decimal, label: str) -> tuple[RequirementStatus, str]:
    g = got.quantize(Decimal("0.01"))
    w = want.quantize(Decimal("0.01"))
    if got >= want:
        return RequirementStatus.SATISFIED, f"{label} {g} >= {w}"
    if want - got <= want * Decimal("0.05"):
        return RequirementStatus.PARTIAL, f"{label} {g} slightly below {w}"
    return RequirementStatus.NOT_SATISFIED, f"{label} {g} < {w}"


def evaluate(requirement: dict[str, Any], profile: ProfileFacts) -> tuple[RequirementStatus, str]:
    """Returns (status, reason). Deterministic; UNKNOWN when required facts are missing."""
    key = _normalized(str(requirement.get("normalized_key", "")))
    operator = str(requirement.get("operator", "")).lower()
    value = requirement.get("value") or {}

    if key in ("academic_cgpa_min", "cgpa_min"):
        required = _num(value.get("min"))
        if profile.cgpa is None or required is None:
            return RequirementStatus.UNKNOWN, "CGPA or required threshold missing"
        # Compare on the same scale: a 3.0/4.0 requirement is not "3.0" on a 10-point scale.
        required_scale = _num(value.get("scale")) or profile.cgpa_scale
        got = profile.cgpa
        want = required
        if (
            required_scale
            and profile.cgpa_scale
            and required_scale != profile.cgpa_scale
            and required_scale > 0
            and profile.cgpa_scale > 0
        ):
            got = (profile.cgpa / profile.cgpa_scale) * Decimal(100)
            want = (required / required_scale) * Decimal(100)
            label = "normalized CGPA%"
            return _threshold_status(got, want, label)
        if got >= want:
            return RequirementStatus.SATISFIED, f"CGPA {got} >= {want} (same scale)"
        gap = want - got
        if profile.cgpa_scale and gap <= profile.cgpa_scale * Decimal("0.05"):
            return RequirementStatus.PARTIAL, f"CGPA {got} slightly below {want}"
        return RequirementStatus.NOT_SATISFIED, f"CGPA {got} < {want}"

    if key in ("language_ielts_overall", "ielts_overall_min"):
        required = _num(value.get("min"))
        if profile.ielts_overall is None or required is None:
            return RequirementStatus.UNKNOWN, "IELTS score or required threshold missing"
        if profile.ielts_overall >= required:
            return RequirementStatus.SATISFIED, f"IELTS {profile.ielts_overall} >= {required}"
        if profile.ielts_overall >= required - Decimal("0.5"):
            return RequirementStatus.PARTIAL, f"IELTS {profile.ielts_overall} within 0.5 of {required}"
        return RequirementStatus.NOT_SATISFIED, f"IELTS {profile.ielts_overall} < {required}"

    if key in ("backlogs_max",):
        max_backlogs = value.get("max", 0)
        try:
            max_b = int(max_backlogs)
        except (TypeError, ValueError):
            return RequirementStatus.UNKNOWN, "Invalid backlog threshold"
        if profile.backlogs <= max_b:
            return RequirementStatus.SATISFIED, f"Backlogs {profile.backlogs} <= {max_b}"
        return RequirementStatus.NOT_SATISFIED, f"Backlogs {profile.backlogs} > {max_b}"

    if key in ("prerequisite_subjects",):
        required_subjects = {_normalized(s) for s in value.get("subjects", [])}
        if not required_subjects:
            return RequirementStatus.NOT_APPLICABLE, "No prerequisite subjects specified"
        if not profile.subjects:
            return RequirementStatus.UNKNOWN, "No subject data in profile"
        missing = required_subjects - {_normalized(s) for s in profile.subjects}
        if not missing:
            return RequirementStatus.SATISFIED, "All required subjects present"
        if len(missing) < len(required_subjects):
            return RequirementStatus.PARTIAL, f"Missing: {sorted(missing)}"
        return RequirementStatus.NOT_SATISFIED, f"Missing: {sorted(missing)}"

    if key in ("min_credits",):
        required = _num(value.get("credits"))
        have = _num(value.get("_matched_credits"))
        if required is None:
            return RequirementStatus.UNKNOWN, "Missing credit requirement"
        if have is None:
            return RequirementStatus.UNKNOWN, "Credit history not provided"
        if have >= required:
            return RequirementStatus.SATISFIED, f"{have} >= {required} credits"
        return RequirementStatus.NOT_SATISFIED, f"{have} < {required} credits"

    if key in ("budget_min", "tuition_max"):
        required = _num(value.get("amount"))
        if profile.total_budget_amount is None or required is None:
            return RequirementStatus.UNKNOWN, "Budget or cost unknown"
        if profile.total_budget_amount >= required:
            return RequirementStatus.SATISFIED, "Budget covers estimated cost"
        if profile.total_budget_amount >= required * Decimal("0.8"):
            return RequirementStatus.PARTIAL, "Budget close to estimated cost"
        return RequirementStatus.NOT_SATISFIED, "Budget materially below estimated cost"

    if key in ("application_deadline",):
        try:
            deadline = date.fromisoformat(str(value.get("date")))
        except (ValueError, TypeError):
            return RequirementStatus.UNKNOWN, "Deadline missing"
        if deadline < date.today():
            return RequirementStatus.NOT_SATISFIED, "Deadline has passed"
        days_left = (deadline - date.today()).days
        if days_left < 14:
            return RequirementStatus.PARTIAL, f"Deadline in {days_left} days"
        return RequirementStatus.SATISFIED, f"Deadline in {days_left} days"

    if operator == "is_set":
        present = any(
            getattr(profile, k, None) is not None
            for k in ("cgpa", "ielts_overall", "total_budget_amount", "graduation_year")
        )
        return (RequirementStatus.SATISFIED if present else RequirementStatus.UNKNOWN), "Presence check"

    return RequirementStatus.UNKNOWN, f"No deterministic rule for key '{key}'"


def evaluate_all(
    requirements: list[dict[str, Any]], profile: ProfileFacts
) -> list[tuple[str, RequirementStatus, str]]:
    out = []
    for req in requirements:
        status, reason = evaluate(req, profile)
        out.append((str(req.get("id", "")), status, reason))
    return out
