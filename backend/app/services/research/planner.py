"""Bounded research query planner. Deterministic; no profile data invented."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.db.models import StudentProfile

MAX_DISCOVERY_QUERIES = 6
MAX_REQUIREMENTS_QUERIES_PER_PROGRAM = 4
MAX_FUNDING_QUERIES = 2
MAX_CAREER_QUERIES = 2

# ISO 3166 alpha-2 -> English country name (static, factual; used to build
# human-readable search queries because queries with codes rank poorly).
COUNTRY_NAMES: dict[str, str] = {
    "DE": "Germany",
    "NL": "Netherlands",
    "AT": "Austria",
    "CH": "Switzerland",
    "FR": "France",
    "IE": "Ireland",
    "US": "United States",
    "GB": "United Kingdom",
    "CA": "Canada",
    "AU": "Australia",
    "IN": "India",
    "SE": "Sweden",
    "DK": "Denmark",
    "FI": "Finland",
    "ES": "Spain",
    "IT": "Italy",
    "BE": "Belgium",
    "PT": "Portugal",
    "PL": "Poland",
    "NO": "Norway",
    "NZ": "New Zealand",
    "SG": "Singapore",
    "CZ": "Czech Republic",
}


def country_display(code: str) -> str:
    return COUNTRY_NAMES.get(code.upper(), code)


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
        name = country_display(country)
        # Measured to return official university program pages (vs. aggregators):
        # "{field} Master's admission requirements university {Country}".
        queries.append(
            PlannedQuery(
                "google",
                f"{field} Master's admission requirements university {name}",
                "discovery",
                {"gl": country.lower()},
            )
        )
        queries.append(
            PlannedQuery(
                "google",
                f"{name} international student visa financial proof official",
                "policy",
            )
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
