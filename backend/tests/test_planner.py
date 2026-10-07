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
