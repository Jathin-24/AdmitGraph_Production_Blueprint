"""Evidence freshness policy. Values are configurable without migrations.

Claim types WITHOUT an entry here (e.g. `policy`, `academic`, `career_goal`
style signals) use the documented 30-day default in `freshness_deadline` —
the fallback is intentional, never an error: an unknown claim type must still
grow stale at a sane, bounded rate.
"""

from datetime import datetime, timedelta

FRESHNESS_WINDOWS_DAYS: dict[str, int] = {
    "deadline": 7,
    "tuition": 30,
    "fee": 30,
    "language": 30,
    "prerequisite": 30,
    "visa": 7,
    "financial_proof": 7,
    "scholarship": 7,
    "career": 14,
}


def freshness_deadline(claim_type: str, retrieved_at: datetime) -> datetime:
    days = FRESHNESS_WINDOWS_DAYS.get(claim_type, 30)
    return retrieved_at + timedelta(days=days)


def is_stale(claim_type: str, retrieved_at: datetime, now: datetime) -> bool:
    return now >= freshness_deadline(claim_type, retrieved_at)
