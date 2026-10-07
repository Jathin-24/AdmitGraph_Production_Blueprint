"""Bounded research query planner. Deterministic; no profile data invented."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.db.models import StudentProfile

MAX_DISCOVERY_QUERIES = 6
MAX_REQUIREMENTS_QUERIES_PER_PROGRAM = 4
MAX_FUNDING_QUERIES = 2
MAX_CAREER_QUERIES = 2


@dataclass
class PlannedQuery:
    engine: str
    q: str
    purpose: str
    parameters: dict[str, Any] = field(default_factory=dict)


def _profile_countries(profile: StudentProfile, preferences: dict[str, Any]) -> list[str]:
    countries = preferences.get("preferred_countries") or []
    if isinstance(countries, list):
        return [str(c) for c in countries]
    return []


def plan_queries(
    profile: StudentProfile, preferences: dict[str, Any], intake_year: int
) -> list[PlannedQuery]:
    field = profile.field_of_study or "graduate"
    countries = _profile_countries(profile, preferences) or [profile.institution_country_code or ""]
    queries: list[PlannedQuery] = []
    for country in countries[:3]:
        if not country:
            continue
        queries.append(
            PlannedQuery(
                "google",
                f"{field} MSc {country} official program admission requirements",
                "discovery",
                {"gl": country.lower()},
            )
        )
        queries.append(
            PlannedQuery("google", f"{country} international student visa financial proof official", "policy")
        )
    queries.append(
        PlannedQuery("google_jobs", f"{field} jobs {countries[0] if countries else ''}".strip(), "career")
    )
    queries.append(PlannedQuery("google_news", f"{field or 'international'} student visa policy", "news"))
    return queries[: MAX_DISCOVERY_QUERIES + MAX_FUNDING_QUERIES + MAX_CAREER_QUERIES]


def plan_program_queries(
    program_name: str, official_domain: str | None, intake_year: int
) -> list[PlannedQuery]:
    site = f"site:{official_domain} " if official_domain else ""
    base = [
        (f"{site}{program_name} admission requirements {intake_year}", "requirements"),
        (f"{site}{program_name} IELTS TOEFL language requirements", "language"),
        (f"{site}{program_name} prerequisites credits modules", "prerequisites"),
        (f"{site}{program_name} application deadline {intake_year} tuition fees", "deadline_tuition"),
    ]
    return [
        PlannedQuery("google", q, purpose) for q, purpose in base[:MAX_REQUIREMENTS_QUERIES_PER_PROGRAM]
    ]
