"""Failure simulator: deterministic counterfactual transformations."""

from __future__ import annotations

from typing import Any

SCENARIOS = {
    "TOP_3_REJECTED",
    "BUDGET_MINUS_25_PERCENT",
    "IELTS_LOWERED",
    "REMOVE_COUNTRY",
    "DEADLINE_MISSED",
    "CUSTOM",
}


def apply_scenario(
    profile_snapshot: dict[str, Any], scenario: str, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Returns a modified profile snapshot (never mutates the input)."""
    modified = dict(profile_snapshot)
    if scenario == "BUDGET_MINUS_25_PERCENT":
        budget = modified.get("total_budget_amount")
        if budget is not None:
            modified["total_budget_amount"] = float(budget) * 0.75
    elif scenario == "IELTS_LOWERED":
        score = modified.get("ielts_overall")
        if score is not None:
            modified["ielts_overall"] = max(0.0, float(score) - 1.0)
    elif scenario == "REMOVE_COUNTRY":
        countries = list(modified.get("preferred_countries") or [])
        if countries:
            modified["preferred_countries"] = countries[1:]
    elif scenario == "DEADLINE_MISSED":
        modified["assume_deadline_missed"] = True
    elif scenario == "CUSTOM":
        if extra:
            modified.update(extra)
    return modified
