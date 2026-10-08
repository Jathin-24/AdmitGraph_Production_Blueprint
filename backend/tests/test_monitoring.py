"""Unit tests for monitoring materiality rules, explanations and schedule math.

Pure functions only — no database, no providers.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.services.monitoring.materiality import (
    FIELD_KEYS,
    explain_change,
    format_value,
    frequency_delta,
    is_material_change,
    material_change_for,
    next_check_time,
    to_scalar,
)

# --------------------------------------------------------------- frequencies


def test_frequency_deltas() -> None:
    assert frequency_delta("DAILY") == timedelta(days=1)
    assert frequency_delta("WEEKLY") == timedelta(days=7)
    assert frequency_delta("MONTHLY") == timedelta(days=30)
    assert frequency_delta("daily") == timedelta(days=1)  # case-insensitive
    assert frequency_delta(None) == timedelta(days=7)  # default WEEKLY
    assert frequency_delta("NEVER") == timedelta(days=7)  # unknown -> default


def test_next_check_time_advances_from_now() -> None:
    now = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    assert next_check_time(now, "WEEKLY") == now + timedelta(days=7)
    assert next_check_time(now, "DAILY") == now + timedelta(days=1)
    assert next_check_time(now, "MONTHLY") == now + timedelta(days=30)


def test_field_keys_match_the_api_contract() -> None:
    assert FIELD_KEYS == ("deadline", "cost", "academic", "prerequisite", "scholarship")


# ------------------------------------------------- legacy-compatible surface


def test_is_material_change_stays_backwards_compatible() -> None:
    assert is_material_change({"a": 1}, {"a": 2}) is True
    assert is_material_change({"a": 1}, {"a": 1}) is False
    assert is_material_change(None, {"a": 1}) is False


# ------------------------------------------------------------- materiality


MATERIALITY_CASES = [
    # deadline: >= 1 calendar day is material
    ("deadline", "2027-01-15", "2027-02-01", True),
    ("deadline", "2027-01-15", "2027-01-16", True),
    ("deadline", "2027-01-15", "2027-01-15", False),
    ("deadline", "2027-01-15", "15 Jan 2027", False),  # same day, other format
    ("deadline", "2027-01-15", "16 Jan 2027", True),
    # cost: any change >= 1 unit, or >= 5%, is material
    ("cost", 12000, 13500, True),
    ("cost", 12000, 12000, False),
    ("cost", 12000, 12000.5, False),  # < 1 unit and < 5%
    ("cost", 10, 10.4, False),  # < 1 unit and 4% < 5%
    ("cost", 10, 10.6, True),  # 6% >= 5%
    ("cost", "€12,000", "€13,500", True),
    ("cost", "€12,000", "€12,000", False),
    # academic / prerequisite: difference after normalization is material
    ("academic", "CGPA 6.5", "cgpa  6.5", False),  # case/whitespace only
    ("academic", "CGPA 6.5", "CGPA 7.0", True),
    ("academic", 6.5, "6.5", False),  # number vs numeric string
    ("prerequisite", "Maths, Physics", "maths, physics", False),
    ("prerequisite", "Maths, Physics", "Maths, Chemistry", True),
    # scholarship: any real difference is a signal
    ("scholarship", "DAAD 12000", "DAAD 12000", False),
    ("scholarship", "DAAD 12000", "DAAD 15000", True),
    # unknown/other keys fall back to text materiality
    ("language", "IELTS 6.5", "IELTS 6.5", False),
    ("language", "IELTS 6.5", "IELTS 7.0", True),
    # nothing observed -> never material
    ("deadline", None, "2027-01-15", False),
    ("deadline", "2027-01-15", None, False),
    ("cost", None, None, False),
]


@pytest.mark.parametrize(("field", "old", "new", "expected"), MATERIALITY_CASES)
def test_materiality_table(field: str, old: object, new: object, expected: bool) -> None:
    assert material_change_for(field, old, new) is expected


# ----------------------------------------------------------- explanations


EXPLANATION_CASES = [
    (
        "deadline",
        "2027-01-15",
        "2027-02-01",
        True,
        "live",
        "Deadline moved from 15 Jan 2027 to 1 Feb 2027",
    ),
    (
        "cost",
        "€12,000",
        "€13,500",
        True,
        "live",
        "Tuition changed from €12,000 to €13,500 (+12.5%)",
    ),
    (
        "cost",
        12000,
        13500,
        True,
        "live",
        "Tuition changed from 12,000 to 13,500 (+12.5%)",
    ),
    ("cost", 13500, 12000, True, "live", "Tuition changed from 13,500 to 12,000 (-11.1%)"),
    ("deadline", "2027-01-15", "2027-02-01", False, "live", "No material change detected"),
    (
        "academic",
        "CGPA 6.5",
        "CGPA 7.0",
        True,
        "live",
        "Academic requirement changed from CGPA 6.5 to CGPA 7.0",
    ),
    (
        "prerequisite",
        "Maths",
        "Chemistry",
        True,
        "live",
        "Prerequisite changed from Maths to Chemistry",
    ),
    (
        "scholarship",
        "DAAD 12000",
        "DAAD 15000",
        True,
        "live",
        "Scholarship information changed from DAAD 12000 to DAAD 15000",
    ),
]


@pytest.mark.parametrize(("field", "old", "new", "material", "source", "expected"), EXPLANATION_CASES)
def test_explanation_generators(
    field: str, old: object, new: object, material: bool, source: str, expected: str
) -> None:
    assert explain_change(field, old, new, material=material, source=source) == expected


def test_cached_source_is_always_marked_honestly() -> None:
    material = explain_change(
        "deadline", "2027-01-15", "2027-02-01", material=True, source="cached"
    )
    immaterial = explain_change("deadline", "2027-01-15", "2027-01-15", material=False,
                                source="cached")
    assert material.endswith("(based on last stored evidence)")
    assert immaterial == "No material change detected (based on last stored evidence)"


def test_format_value_never_invents_values() -> None:
    assert format_value("2027-01-15", "deadline") == "15 Jan 2027"
    assert format_value(12000, "cost") == "12,000"
    assert format_value("€12,000", "cost") == "€12,000"
    assert format_value(None, "deadline") == "unknown"


def test_to_scalar_collapses_extracted_dicts_onto_the_field() -> None:
    assert to_scalar({"date": "2027-01-15"}, "deadline") == "2027-01-15"
    assert to_scalar({"application_deadline": "2027-01-15", "intake": "2027"},
                     "deadline") == "2027-01-15"
    assert to_scalar({"min": 6.5}, "academic") == 6.5
    assert to_scalar({"tuition_max": 12000, "currency": "EUR"}, "cost") == 12000
    assert to_scalar("  spaced   text  ") == "spaced text"
    assert to_scalar(None) is None
