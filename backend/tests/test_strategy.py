from decimal import Decimal

from app.db.models import ProgramCategory
from app.services.strategy.health import plan_health_score
from app.services.strategy.portfolio import Candidate, build_portfolio, categorize


def test_portfolio_categories() -> None:
    assert categorize(Decimal("30")) == ProgramCategory.REACH
    assert categorize(Decimal("60")) == ProgramCategory.TARGET
    assert categorize(Decimal("80")) == ProgramCategory.LOWER_RISK


def test_portfolio_diversification_bounds() -> None:
    candidates = [Candidate(f"p{i}", Decimal(90 - i)) for i in range(20)]
    portfolio = build_portfolio(candidates)
    assert len(portfolio[ProgramCategory.REACH]) <= 2
    assert len(portfolio[ProgramCategory.TARGET]) <= 4
    assert len(portfolio[ProgramCategory.LOWER_RISK]) <= 2


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
