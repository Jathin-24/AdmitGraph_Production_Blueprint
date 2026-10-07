from datetime import date
from decimal import Decimal


def plan_health_score(
    *,
    blocker_count: int,
    unresolved_conflicts: int,
    stale_evidence_count: int,
    nearest_deadline: date | None,
    financial_feasible: bool,
    documents_ready: bool,
    today: date | None = None,
) -> Decimal:
    today = today or date.today()
    score = Decimal(100)
    score -= min(40, blocker_count * 20)
    score -= min(20, unresolved_conflicts * 10)
    score -= min(15, stale_evidence_count * 5)
    if nearest_deadline is not None:
        days = (nearest_deadline - today).days
        if days < 0:
            score -= 30
        elif days < 14:
            score -= 10
    if not financial_feasible:
        score -= 20
    if not documents_ready:
        score -= 10
    return max(Decimal(0), score).quantize(Decimal("0.01"))
