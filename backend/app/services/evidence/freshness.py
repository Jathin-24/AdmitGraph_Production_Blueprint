"""Evidence freshness policy. Values are configurable without migrations."""

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
