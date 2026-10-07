from datetime import date, timedelta
from decimal import Decimal

from app.db.models import RequirementStatus, RiskSeverity
from app.services.risk.engine import RiskContext, assess


def test_mandatory_not_satisfied_is_critical_or_high() -> None:
    risks = assess(RiskContext(eligibility=[("language_ielts", RequirementStatus.NOT_SATISFIED, True)]))
    assert any(r.severity in (RiskSeverity.CRITICAL, RiskSeverity.HIGH) for r in risks)


def test_unknown_requirement_medium_data_quality() -> None:
    risks = assess(RiskContext(eligibility=[("prerequisites", RequirementStatus.UNKNOWN, True)]))
    assert any(r.risk_type == "DATA_QUALITY" and r.severity == RiskSeverity.MEDIUM for r in risks)


def test_deadline_passed_critical() -> None:
    risks = assess(RiskContext(next_deadline=date.today() - timedelta(days=1)))
    assert any(r.severity == RiskSeverity.CRITICAL and r.risk_type == "DEADLINE" for r in risks)


def test_low_budget_financial_risk() -> None:
    risks = assess(RiskContext(budget=Decimal("1000000"), estimated_cost=Decimal("2000000")))
    assert any(r.risk_type == "FINANCIAL" and r.severity == RiskSeverity.HIGH for r in risks)


def test_stale_evidence_medium() -> None:
    risks = assess(RiskContext(stale_evidence_count=3))
    assert any(r.risk_type == "INFORMATION_FRESHNESS" for r in risks)
