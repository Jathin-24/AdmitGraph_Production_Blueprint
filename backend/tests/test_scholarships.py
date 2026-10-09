"""W13 scholarship finder — dataset integrity + service contract (no DB).

Two things are pinned here, both acceptance criteria from PLAN.md:

1. **Honesty of the dataset** — every entry carries a real ``https://`` source
   URL, the required fields, valid enumerations and a deadline that is either
   null or a real ISO date. A row that cannot satisfy that fails the suite,
   which is the point: nothing unsubstantiated may ship.
2. **The service contract** — case-insensitive ``q``/country/degree/funding
   filters, the programs-shaped envelope {items, page, page_size, total,
   next_cursor}, and the hard page_size cap of 100.

No database, no network, no FastAPI session: everything runs off the JSON file.
"""

from __future__ import annotations

import re
from datetime import date

import pytest

from app.services import scholarships as svc

REQUIRED_FIELDS = (
    "id",
    "name",
    "provider",
    "country",
    "degree_levels",
    "funding_type",
    "amount_text",
    "deadline",
    "eligibility",
    "source_url",
    "last_checked",
)

SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
COUNTRY_RE = re.compile(r"[A-Z]{2}")
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


@pytest.fixture(scope="module")
def rows() -> list[svc.Scholarship]:
    return svc.load_scholarships()


@pytest.fixture(scope="module")
def meta() -> dict[str, object]:
    return svc.meta()


# --------------------------------------------------------------- integrity


def test_dataset_holds_between_10_and_20_entries(rows: list[svc.Scholarship]) -> None:
    assert 10 <= len(rows) <= 20, f"expected 10-20 verified entries, found {len(rows)}"


def test_every_entry_carries_the_required_fields(rows: list[svc.Scholarship]) -> None:
    for row in rows:
        for field in REQUIRED_FIELDS:
            assert field in row, f"{row.get('id', '?')} is missing {field}"


def test_ids_are_unique_slugs(rows: list[svc.Scholarship]) -> None:
    ids = [row["id"] for row in rows]
    assert len(ids) == len(set(ids))
    for row_id in ids:
        assert SLUG_RE.fullmatch(row_id), f"{row_id} is not a slug"


def test_every_source_url_is_https(rows: list[svc.Scholarship]) -> None:
    """The whole point of W13: a real, resolvable, official source per row."""
    for row in rows:
        assert row["source_url"].startswith("https://"), row["id"]
        assert len(row["source_url"]) > len("https://") + 4, row["id"]
        if row["details_url"] is not None:
            assert row["details_url"].startswith("https://"), row["id"]


def test_names_providers_and_eligibility_are_present_and_sourced_sounding(
    rows: list[svc.Scholarship],
) -> None:
    for row in rows:
        assert row["name"].strip(), row["id"]
        assert row["provider"].strip(), row["id"]
        assert row["eligibility"].strip(), row["id"]
        # 2-3 factual sentences per the W13 field contract.
        sentences = [s for s in SENTENCE_RE.split(row["eligibility"]) if s.strip()]
        assert 2 <= len(sentences) <= 4, f"{row['id']} has {len(sentences)} sentences"
        if row["amount_text"] is not None:
            assert row["amount_text"].strip()


def test_degree_levels_and_funding_types_are_valid_enums(
    rows: list[svc.Scholarship],
) -> None:
    for row in rows:
        assert row["degree_levels"], f"{row['id']} has no degree level"
        assert len(row["degree_levels"]) == len(set(row["degree_levels"])), row["id"]
        for level in row["degree_levels"]:
            assert level in svc.DEGREE_LEVELS, f"{row['id']}: unknown level {level}"
        assert row["funding_type"] in svc.FUNDING_TYPES, row["id"]


def test_country_is_a_two_letter_code_or_null(rows: list[svc.Scholarship]) -> None:
    """null = a genuinely multi-country scheme (e.g. Erasmus Mundus): naming one
    destination would be a fabrication, so the field stays empty instead."""
    for row in rows:
        if row["country"] is None:
            continue
        assert COUNTRY_RE.fullmatch(row["country"]), f"{row['id']}: {row['country']}"


def test_deadline_is_null_or_a_real_iso_date(rows: list[svc.Scholarship]) -> None:
    for row in rows:
        if row["deadline"] is None:
            continue
        try:
            date.fromisoformat(row["deadline"])
        except ValueError as exc:  # noqa: PERF203 - one iteration per bad row
            raise AssertionError(f"{row['id']}: {row['deadline']} is not YYYY-MM-DD") from exc


def test_every_row_carries_the_dataset_check_date(
    rows: list[svc.Scholarship], meta: dict[str, object]
) -> None:
    last_checked = str(meta.get("last_checked") or "")
    date.fromisoformat(last_checked)  # a real date, not free text
    assert last_checked, "_meta.last_checked is missing"
    for row in rows:
        assert row["last_checked"] == last_checked, row["id"]


def test_meta_states_that_figures_must_be_re_verified(meta: dict[str, object]) -> None:
    note = " ".join(str(value) for value in meta.values()).lower()
    assert "re-verify" in note or "reverify" in note


# ----------------------------------------------------------------- filters


def test_q_is_case_insensitive_over_name_provider_and_eligibility(
    rows: list[svc.Scholarship],
) -> None:
    by_name = svc.filter_scholarships(rows, q="CHEVENING")
    assert [row["id"] for row in by_name] == ["chevening-scholarships"]

    by_provider = svc.filter_scholarships(rows, q="swedish institute")
    assert [row["id"] for row in by_provider] == ["si-scholarship-global-professionals"]

    by_eligibility = svc.filter_scholarships(rows, q="2,800 hours")
    assert [row["id"] for row in by_eligibility] == ["chevening-scholarships"]

    assert svc.filter_scholarships(rows, q="   ") == rows


def test_q_searches_only_the_three_scanned_fields(rows: list[svc.Scholarship]) -> None:
    # "€5,000" appears in amount_text but not in name/provider/eligibility.
    hits = svc.filter_scholarships(rows, q="5,000")
    assert hits == []


def test_country_filter_matches_exactly_and_case_insensitively(
    rows: list[svc.Scholarship],
) -> None:
    german = svc.filter_scholarships(rows, country="DE")
    assert {row["id"] for row in german} == {
        "daad-epos-development-related-postgraduate-courses",
        "deutschlandstipendium",
        "heinrich-boell-foundation-scholarship",
    }
    assert svc.filter_scholarships(rows, country="de") == german


def test_multi_country_rows_never_match_a_country_filter(
    rows: list[svc.Scholarship],
) -> None:
    """Erasmus Mundus has no single destination — it must not appear when the
    student narrows to a country, and it must appear when no country is asked."""
    erasmus = [row for row in rows if row["country"] is None]
    assert erasmus, "dataset should contain at least one multi-country scheme"
    erasmus_ids = {row["id"] for row in erasmus}
    for code in ("DE", "NL", "US", "GB", "FR"):
        filtered_ids = {row["id"] for row in svc.filter_scholarships(rows, country=code)}
        assert not filtered_ids & erasmus_ids, f"multi-country row leaked into {code}"
    assert {row["id"] for row in svc.filter_scholarships(rows, q="Erasmus Mundus")} == {
        row["id"] for row in erasmus
    }


def test_degree_level_and_funding_type_filters(rows: list[svc.Scholarship]) -> None:
    phd = svc.filter_scholarships(rows, degree_level="phd")
    assert {row["id"] for row in phd} == {
        "daad-epos-development-related-postgraduate-courses",
        "fulbright-foreign-student-program",
        "heinrich-boell-foundation-scholarship",
        "stipendium-hungaricum",
        "turkiye-scholarships",
    }

    full = svc.filter_scholarships(rows, funding_type="full")
    assert len(full) == 7
    assert svc.filter_scholarships(rows, funding_type="FULL") == full
    assert svc.filter_scholarships(rows, funding_type="merit") == [
        row for row in rows if row["id"] == "deutschlandstipendium"
    ]


def test_unknown_filter_values_match_nothing_instead_of_guessing(
    rows: list[svc.Scholarship],
) -> None:
    assert svc.filter_scholarships(rows, degree_level="mba") == []
    assert svc.filter_scholarships(rows, funding_type="scholarship") == []
    assert svc.filter_scholarships(rows, country="XX") == []


def test_filters_combine(rows: list[svc.Scholarship]) -> None:
    combined = svc.filter_scholarships(rows, country="DE", degree_level="phd")
    assert {row["id"] for row in combined} == {
        "daad-epos-development-related-postgraduate-courses",
        "heinrich-boell-foundation-scholarship",
    }
    assert svc.filter_scholarships(rows, country="DE", funding_type="need-based") == []


# --------------------------------------------------------------- envelope


def test_envelope_shape_matches_the_programs_endpoint(
    rows: list[svc.Scholarship],
) -> None:
    page = svc.list_scholarships()
    assert set(page) == {"items", "page", "page_size", "total", "next_cursor"}
    assert page["page"] == 1
    assert page["page_size"] == 20
    assert page["total"] == len(rows)
    assert page["next_cursor"] is None  # 11 rows fit in one page of 20
    assert len(page["items"]) == len(rows)
    assert all(isinstance(item, dict) for item in page["items"])


def test_paging_walks_the_whole_dataset(rows: list[svc.Scholarship]) -> None:
    first = svc.list_scholarships(page=1, page_size=5)
    assert len(first["items"]) == 5
    assert first["total"] == len(rows)
    assert first["next_cursor"] == "2"

    second = svc.list_scholarships(page=2, page_size=5)
    assert len(second["items"]) == 5
    assert second["next_cursor"] == "3"

    last = svc.list_scholarships(page=3, page_size=5)
    assert len(last["items"]) == len(rows) - 10
    assert last["next_cursor"] is None

    # Pages never overlap and never lose a row.
    seen = [item["id"] for page in (first, second, last) for item in page["items"]]
    assert seen == [row["id"] for row in rows]


def test_page_size_is_capped_at_100(rows: list[svc.Scholarship]) -> None:
    page = svc.list_scholarships(page=1, page_size=5000)
    assert page["page_size"] == 100
    assert len(page["items"]) == len(rows)

    tiny = svc.list_scholarships(page=1, page_size=0)
    assert tiny["page_size"] == 1


def test_page_is_floored_at_one(rows: list[svc.Scholarship]) -> None:
    page = svc.list_scholarships(page=0, page_size=5)
    assert page["page"] == 1
    assert [item["id"] for item in page["items"]] == [row["id"] for row in rows[:5]]


def test_an_empty_filter_result_still_returns_a_valid_envelope() -> None:
    page = svc.list_scholarships(q="no-such-scholarship-anywhere", country="DE")
    assert page == {
        "items": [],
        "page": 1,
        "page_size": 20,
        "total": 0,
        "next_cursor": None,
    }


def test_items_carry_the_source_and_check_date_for_the_ui() -> None:
    item = svc.list_scholarships(q="chevening")["items"][0]
    assert item["source_url"].startswith("https://")
    assert item["last_checked"] == str(svc.meta().get("last_checked"))
    assert item["deadline"] is None or re.fullmatch(r"\d{4}-\d{2}-\d{2}", item["deadline"])
