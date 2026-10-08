"""Bounded research query planner. Deterministic; no profile data invented.

Every query is generated from profile/goal data (countries, field, intake year).
No university facts (deadlines, fees, requirements, visa rules) are asserted by
these templates - they only ask a search engine where to look. `UNKNOWN` stays a
valid downstream outcome when a search finds nothing.

Budgets (per serpapi_docs/SERPAPI_INTEGRATION.md "API usage budget"):
  - discovery:            <= MAX_DISCOVERY_QUERIES per run (incl. page-2)
  - requirements/program: <= MAX_REQUIREMENTS_QUERIES_PER_PROGRAM per program
  - funding:              <= MAX_FUNDING_QUERIES per run
  - career:               <= MAX_CAREER_QUERIES per run
  - news:                 <= MAX_NEWS_QUERIES per run
  - policy:               <= MAX_POLICY_QUERIES per run (app-level safeguard;
                           the provider doc does not bound policy explicitly)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.db.models import StudentProfile
from app.services.research.source_profiles import government_portal, study_portal

MAX_DISCOVERY_QUERIES = 6
MAX_REQUIREMENTS_QUERIES_PER_PROGRAM = 4
MAX_FUNDING_QUERIES = 2
MAX_CAREER_QUERIES = 2
MAX_NEWS_QUERIES = 2
MAX_POLICY_QUERIES = 2

# Per-purpose budgets keyed by PlannedQuery.purpose. The orchestrator enforces
# these when executing planned queries; program-scope purposes are bounded per
# program instead of per run.
RUN_BUDGETS: dict[str, int] = {
    "discovery": MAX_DISCOVERY_QUERIES,
    "policy": MAX_POLICY_QUERIES,
    "funding": MAX_FUNDING_QUERIES,
    "career": MAX_CAREER_QUERIES,
    "news": MAX_NEWS_QUERIES,
}
PROGRAM_BUDGET = MAX_REQUIREMENTS_QUERIES_PER_PROGRAM

# --- Query templates -------------------------------------------------------
# `{...}` placeholders are filled from profile/goal data only. Placeholders that
# depend on data the planner does not know (e.g. official_domain) are filled by
# the orchestrator at execution time, or the query falls back to a non-site form.

DISCOVERY_TEMPLATE = "{field} Master's admission requirements university {country}"
OFFICIAL_SITE_DISCOVERY_TEMPLATE = "site:{official_domain} {field} Master's admission requirements"

# The 5 official-site requirement templates (serpapi_docs "Search query templates").
# The program title is quoted and the intake year is omitted on site: queries:
# live SerpApi probes showed Google silently drops the `site:` restriction on
# loose multi-term queries, while a quoted title keeps it — and the year was
# the term that correlated with the drop.
REQUIREMENT_TEMPLATES: list[tuple[str, str]] = [
    ('site:{official_domain} "{program}" admission requirements', "requirements"),
    ('site:{official_domain} "{program}" IELTS TOEFL language requirements', "language"),
    ('site:{official_domain} "{program}" prerequisites credits modules', "prerequisites"),
    ('site:{official_domain} "{program}" application deadline', "deadline_tuition"),
    ('site:{official_domain} "{program}" tuition fees', "deadline_tuition"),
]

# Program-free wording: the official-domain scope already targets the right
# university, and adding the program title as a phrase made Google return 0
# results (observed live), starving the funding bucket entirely.
SCHOLARSHIP_TEMPLATE = "site:{official_domain} international students scholarship"
SCHOLARSHIP_FALLBACK_TEMPLATE = "{country} international students scholarship {field}"

COUNTRY_POLICY_TEMPLATE = "{country} international student visa financial proof official"
GOV_SITE_POLICY_TEMPLATE = "site:{official_government_domain} student visa financial proof"

CAREER_TEMPLATE = "{field} jobs {city} {country}"

# Ordered by priority; the planner picks the top MAX_NEWS_QUERIES within budget.
NEWS_TEMPLATES: list[str] = [
    "{country} student visa policy international students",
    "{country} university tuition international students",
    "{country} university admissions policy {field}",
]

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


# Common field abbreviations -> full degree wording (static, factual; searches
# for "AI Master's" rank aggregator listicles, "Artificial Intelligence
# Master's" ranks university pages).
FIELD_DISPLAY: dict[str, str] = {
    "AI": "Artificial Intelligence",
    "CS": "Computer Science",
    "ML": "Machine Learning",
    "DS": "Data Science",
    "IT": "Information Technology",
    "MBA": "Business Administration",
}


def field_display(field: str) -> str:
    return FIELD_DISPLAY.get(field.strip(), field)


@dataclass
class PlannedQuery:
    engine: str
    q: str
    purpose: str
    parameters: dict[str, Any] = field(default_factory=dict)
    template: str | None = None
    budget: str = "discovery"  # which run/program budget this query is charged to

    def as_dict(self) -> dict[str, Any]:
        """Serializable entry stored on ResearchPlan.planned_queries."""
        return {
            "engine": self.engine,
            "q": self.q,
            "purpose": self.purpose,
            "template": self.template,
            "parameters": dict(self.parameters),
            "budget": self.budget,
        }


def _profile_countries(profile: StudentProfile, preferences: dict[str, Any]) -> list[str]:
    countries = preferences.get("preferred_countries") or []
    if isinstance(countries, list):
        return [str(c) for c in countries if str(c).strip()]
    return []


def plan_queries(
    profile: StudentProfile, preferences: dict[str, Any], intake_year: int
) -> list[PlannedQuery]:
    """Bounded, profile-driven query plan. Never hard-codes one destination."""
    field_name = field_display(profile.field_of_study or "graduate")
    countries = _profile_countries(profile, preferences) or [
        (profile.institution_country_code or "").strip()
    ]
    countries = [c for c in countries if c][:3]
    cities = [str(c) for c in (preferences.get("preferred_cities") or []) if str(c).strip()]

    queries: list[PlannedQuery] = []

    # --- discovery (<= MAX_DISCOVERY_QUERIES). A non-site query always runs
    # first (fallback); the national study portal (routing config only) gets a
    # site: variant. The orchestrator may add a university-domain site: query
    # and page-2 entries later, still inside this same budget. ---
    discovery_used = 0
    for country in countries:
        if discovery_used >= MAX_DISCOVERY_QUERIES:
            break
        name = country_display(country)
        # Measured to return official university program pages (vs. aggregators):
        # "{field} Master's admission requirements university {Country}".
        queries.append(
            PlannedQuery(
                "google",
                DISCOVERY_TEMPLATE.format(field=field_name, country=name),
                "discovery",
                {"gl": country.lower()},
                template=DISCOVERY_TEMPLATE,
                budget="discovery",
            )
        )
        discovery_used += 1
        portal = study_portal(country)
        if portal and discovery_used < MAX_DISCOVERY_QUERIES:
            queries.append(
                PlannedQuery(
                    "google",
                    OFFICIAL_SITE_DISCOVERY_TEMPLATE.format(
                        field=field_name, official_domain=portal
                    ),
                    "discovery",
                    {"gl": country.lower()},
                    template=OFFICIAL_SITE_DISCOVERY_TEMPLATE,
                    budget="discovery",
                )
            )
            discovery_used += 1

    # --- country policy (always keeps a non-site fallback query) ---
    policy_budget = MAX_POLICY_QUERIES
    for country in countries:
        if policy_budget <= 0:
            break
        name = country_display(country)
        queries.append(
            PlannedQuery(
                "google",
                COUNTRY_POLICY_TEMPLATE.format(country=name),
                "policy",
                template=COUNTRY_POLICY_TEMPLATE,
                budget="policy",
            )
        )
        policy_budget -= 1
        gov = government_portal(country)
        if gov and policy_budget > 0:
            queries.append(
                PlannedQuery(
                    "google",
                    GOV_SITE_POLICY_TEMPLATE.format(official_government_domain=gov),
                    "policy",
                    template=GOV_SITE_POLICY_TEMPLATE,
                    budget="policy",
                )
            )
            policy_budget -= 1

    # --- funding/scholarship (<= MAX_FUNDING_QUERIES per run). One slot is
    # reserved for a site: scholarship query once a shortlisted program's
    # official domain is known; the orchestrator issues it during normalization
    # through funding.scholarship_query_for (budget-checked + deduplicated). ---
    if countries and MAX_FUNDING_QUERIES > 1:
        name = country_display(countries[0])
        queries.append(
            PlannedQuery(
                "google",
                SCHOLARSHIP_FALLBACK_TEMPLATE.format(country=name, field=field_name),
                "funding",
                template=SCHOLARSHIP_FALLBACK_TEMPLATE,
                budget="funding",
            )
        )

    # --- career (google_jobs engine, <= MAX_CAREER_QUERIES) ---
    for country in countries[:MAX_CAREER_QUERIES]:
        name = country_display(country)
        city = cities[0] if cities else ""
        queries.append(
            PlannedQuery(
                "google_jobs",
                CAREER_TEMPLATE.format(field=field_name, city=city, country=name).replace("  ", " ").strip(),
                "career",
                template=CAREER_TEMPLATE,
                budget="career",
            )
        )

    # --- news (top templates within <= MAX_NEWS_QUERIES) ---
    if countries:
        name = country_display(countries[0])
        for template in NEWS_TEMPLATES[:MAX_NEWS_QUERIES]:
            queries.append(
                PlannedQuery(
                    "google_news",
                    template.format(country=name, field=field_name),
                    "news",
                    template=template,
                    budget="news",
                )
            )

    return queries


def plan_program_queries(
    program_name: str, official_domain: str | None, intake_year: int
) -> list[PlannedQuery]:
    """Official-site requirement queries for one shortlisted program.

    Bounded by MAX_REQUIREMENTS_QUERIES_PER_PROGRAM: the five templates are
    covered by four queries (application deadline + tuition fees are merged so
    the per-program budget is never exceeded).

    The program title is quoted so Google keeps the ``site:`` restriction
    (live probes: unquoted multi-term queries silently drop it). The intake
    year is appended only to the non-site fallback, where there is no site:
    operator at risk.
    """
    site = f"site:{official_domain} " if official_domain else ""
    title = '"' + " ".join(program_name.replace('"', " ").split()) + '"'
    year = "" if official_domain else f" {intake_year}"
    base = [
        (
            f"{site}{title} admission requirements{year}",
            "requirements",
            REQUIREMENT_TEMPLATES[0][0],
        ),
        (
            f"{site}{title} IELTS TOEFL language requirements",
            "language",
            REQUIREMENT_TEMPLATES[1][0],
        ),
        (
            f"{site}{title} prerequisites credits modules",
            "prerequisites",
            REQUIREMENT_TEMPLATES[2][0],
        ),
        (
            f"{site}{title} application deadline tuition fees{year}",
            "deadline_tuition",
            f"{REQUIREMENT_TEMPLATES[3][0]} | {REQUIREMENT_TEMPLATES[4][0]}",
        ),
    ]
    return [
        PlannedQuery("google", q, purpose, template=template, budget=f"program:{program_name}")
        for q, purpose, template in base[:MAX_REQUIREMENTS_QUERIES_PER_PROGRAM]
    ]


def plan_scholarship_query(
    program_name: str, official_domain: str | None, country_name: str, field_name: str
) -> PlannedQuery | None:
    """Site-restricted scholarship query (SCHOLARSHIP_TEMPLATE), or the
    non-site fallback when no official domain is known. Returns None when the
    caller has no budget left for funding searches."""
    if official_domain:
        return PlannedQuery(
            "google",
            SCHOLARSHIP_TEMPLATE.format(official_domain=official_domain),
            "funding",
            template=SCHOLARSHIP_TEMPLATE,
            budget="funding",
        )
    return PlannedQuery(
        "google",
        SCHOLARSHIP_FALLBACK_TEMPLATE.format(country=country_name, field=field_name),
        "funding",
        template=SCHOLARSHIP_FALLBACK_TEMPLATE,
        budget="funding",
    )
