"""Policy signal service: country visa/financial-proof query planning + summary.

Query templates are routing configuration (which official page to ask for), not
data claims: no visa rule, deadline or financial-proof amount is asserted here.
Bounded by MAX_POLICY_QUERIES per run; planning lives in planner.plan_queries.
"""

from __future__ import annotations

from typing import Any

from app.services.research.planner import MAX_POLICY_QUERIES


def policy_summary(search_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize stored policy searches for a research run's step output."""
    policy_runs = [r for r in search_results if r.get("purpose") == "policy"]
    return {
        "policy_queries_run": len(policy_runs),
        "policy_queries_budget": MAX_POLICY_QUERIES,
        "queries": [str(r.get("query", "")) for r in policy_runs],
    }
