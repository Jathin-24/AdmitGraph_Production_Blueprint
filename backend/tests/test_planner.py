from types import SimpleNamespace

from app.db.models import StudentProfile
from app.services.research.planner import (
    country_display,
    field_display,
    plan_program_queries,
    plan_queries,
)


def _profile(**kwargs: object) -> StudentProfile:
    data = {
        "field_of_study": "AI",
        "institution_country_code": "IN",
        "graduation_year": 2027,
    }
    data.update(kwargs)
    return SimpleNamespace(**data)  # type: ignore[return-value]


def test_discovery_query_is_human_readable_and_universal() -> None:
    queries = plan_queries(_profile(), {"preferred_countries": ["DE"]}, 2027)
    discovery = [q for q in queries if q.purpose == "discovery"]
    assert discovery
    q = discovery[0].q
    # Abbreviations expanded, country spelled out, university-biased phrasing.
    assert "Artificial Intelligence" in q
    assert "AI " not in q
    assert "Germany" in q
    assert "university" in q
    assert discovery[0].parameters.get("gl") == "de"


def test_purposes_are_tagged() -> None:
    queries = plan_queries(_profile(), {"preferred_countries": ["DE"]}, 2027)
    purposes = {q.purpose for q in queries}
    assert {"discovery", "policy", "career", "news"} <= purposes


def test_country_and_field_display_helpers() -> None:
    assert country_display("DE") == "Germany"
    assert country_display("ZZ") == "ZZ"
    assert field_display("AI") == "Artificial Intelligence"
    assert field_display("Civil Engineering") == "Civil Engineering"


def test_program_queries_bounded() -> None:
    queries = plan_program_queries("MSc Test", "uni.example", 2027)
    assert len(queries) <= 4
    allowed = ("requirements", "language", "prerequisites", "deadline_tuition")
    assert all(q.purpose in allowed for q in queries)
    assert all("site:uni.example" in q.q for q in queries)


# --- budgets, engines and templates (serpapi_docs "API usage budget") -------

_ENGINES_BY_PURPOSE = {
    "discovery": "google",
    "policy": "google",
    "funding": "google",
    "career": "google_jobs",
    "news": "google_news",
}


def _queries(countries: list[str]) -> list:
    return plan_queries(_profile(), {"preferred_countries": countries}, 2027)


def test_every_purpose_stays_within_its_run_budget() -> None:
    from collections import Counter

    from app.services.research.planner import RUN_BUDGETS

    queries = _queries(["DE", "NL", "CH", "US"])
    counts = Counter(q.purpose for q in queries)
    assert counts
    for purpose, cap in RUN_BUDGETS.items():
        assert counts[purpose] <= cap, f"{purpose} planned {counts[purpose]} > {cap}"


def test_budget_labels_match_purposes_and_are_known() -> None:
    from app.services.research.planner import RUN_BUDGETS

    queries = _queries(["DE", "NL", "US"])
    for q in queries:
        assert q.budget == q.purpose
        assert q.budget in RUN_BUDGETS


def test_engine_matches_purpose() -> None:
    for q in _queries(["DE", "NL"]):
        assert q.engine == _ENGINES_BY_PURPOSE[q.purpose], q


def test_templates_are_placeholders_never_hardcoded_facts() -> None:
    for q in _queries(["DE", "NL", "CH"]):
        # The executed query is fully formatted: no unresolved placeholder.
        assert "{" not in q.q and "}" not in q.q
        if q.template is not None:
            assert "{" in q.template, f"template without profile placeholder: {q.template}"
        # Templates ask where to look; they never assert a fee, deadline or rule.
        lowered = q.q.lower()
        assert not any(bad in lowered for bad in ("deadline is", "tuition is", "$", "€", "free visa"))


def test_country_portal_is_site_routing_only() -> None:
    discovery = [q for q in _queries(["DE"]) if q.purpose == "discovery"]
    # Germany's national portal is appended as a site: variant; the plain query
    # still runs first, so an unknown portal can never lose coverage.
    assert "site:daad.de" in discovery[1].q
    assert "site:" not in discovery[0].q

    policy = [q for q in _queries(["CH"]) if q.purpose == "policy"]
    assert any("site:admin.ch" in q.q for q in policy)
    assert any("site:" not in q.q for q in policy)


def test_unknown_country_falls_back_to_unrestricted_queries() -> None:
    queries = _queries(["ZZ"])
    assert queries
    assert all("site:" not in q.q for q in queries)
    assert {q.purpose for q in queries} >= {"discovery", "policy"}


def test_program_query_templates_cover_five_templates_in_four_queries() -> None:
    from app.services.research.planner import REQUIREMENT_TEMPLATES

    queries = plan_program_queries("MSc Test", "uni.example", 2027)
    assert len(queries) == 4
    # Every one of the 5 documented templates appears in a query template.
    joined = " | ".join(q.template or "" for q in queries)
    for template, _purpose in REQUIREMENT_TEMPLATES:
        assert template in joined
    assert all(q.budget.startswith("program:") for q in queries)
    # deadline + tuition are merged so the per-program budget is never exceeded.
    merged = next(q for q in queries if q.purpose == "deadline_tuition")
    assert "application deadline" in (merged.template or "") and "tuition fees" in (
        merged.template or ""
    )


def test_program_queries_quote_title_to_hold_site_operator() -> None:
    # Live SerpApi probes: Google drops site: on loose multi-term queries but
    # keeps it when the program title is quoted as a phrase. The intake year
    # was the term correlated with the drop, so site: queries stay year-free.
    queries = plan_program_queries('M.Sc. "Weird" Title', "uni.example", 2027)
    assert all(q.q.startswith('site:uni.example "M.Sc. Weird Title"') for q in queries)
    assert all("2027" not in q.q for q in queries)
    assert all('""' not in q.q for q in queries)

    # Non-site fallback keeps the year: there is no site: operator to lose.
    fallback = plan_program_queries("MSc Test", None, 2027)
    assert all('"' in q.q for q in fallback)
    assert any("2027" in q.q for q in fallback)


def test_program_queries_without_known_domain_still_run() -> None:
    queries = plan_program_queries("MSc Test", None, 2027)
    assert len(queries) == 4
    assert all("site:" not in q.q for q in queries)
    assert all(q.q for q in queries)


def test_scholarship_query_uses_budget_and_site_routing() -> None:
    from app.services.research.funding import scholarship_query_for
    from app.services.research.planner import SCHOLARSHIP_TEMPLATE, plan_scholarship_query

    site = plan_scholarship_query("MSc Test", "uni.example", "Germany", "Artificial Intelligence")
    assert site is not None and site.q.startswith("site:uni.example ")
    assert site.template == SCHOLARSHIP_TEMPLATE and site.budget == "funding"

    fallback = plan_scholarship_query("MSc Test", None, "Germany", "Artificial Intelligence")
    assert fallback is not None and "site:" not in fallback.q

    assert (
        scholarship_query_for(
            "MSc Test", "uni.example", "Germany", "AI", budget_remaining=0
        )
        is None
    )


def test_planned_query_serializes_for_the_run() -> None:
    for q in _queries(["DE"]):
        entry = q.as_dict()
        assert set(entry) == {"engine", "q", "purpose", "template", "parameters", "budget"}
        assert entry["q"] == q.q
        assert entry["parameters"] == q.parameters
