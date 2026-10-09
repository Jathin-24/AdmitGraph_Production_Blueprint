"""Audit D-2: deterministic degree-level derivation for normalized programs.

`derive_degree_type` is the only rule allowed to write `Program.degree_type`
during a research run: a title keyword maps to a canonical level, anything
unclear stays NULL (never guessed).
"""

from __future__ import annotations

import pytest

from app.services.research.normalize import derive_degree_type


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("M.Sc. Artificial Intelligence", "MASTERS"),
        ("Computer Science M.Sc.", "MASTERS"),
        ("MSc Data Science", "MASTERS"),
        ("M. Sc. Robotics", "MASTERS"),
        ("Master of Science in Physics", "MASTERS"),
        ("Masters Digital Engineering", "MASTERS"),
        ("Master's Sustainable Energy", "MASTERS"),
        ("MBA International Management", "MASTERS"),
        ("M.Eng. Mechanical Engineering", "MASTERS"),
        ("B.Sc. Mechanical Engineering", "BACHELORS"),
        ("BSc History", "BACHELORS"),
        ("Bachelor of Arts in Sociology", "BACHELORS"),
        ("Bachelors of Engineering", "BACHELORS"),
        ("PhD in Robotics", "PHD"),
        ("Ph.D. Computer Science", "PHD"),
        ("Doctoral Candidate in Chemistry", "PHD"),
        ("Dr.-Ing. Electrical Engineering", "PHD"),
    ],
)
def test_known_degree_titles_map_to_a_canonical_level(title: str, expected: str) -> None:
    assert derive_degree_type(title) == expected


@pytest.mark.parametrize(
    "title",
    [
        "Exchange Degree Programme",
        "Diploma Supplement",
        "International Student Scholarships - DAAD",
        "Admission Requirements Roundup",
    ],
)
def test_titles_that_name_no_level_stay_null(title: str) -> None:
    """No deterministic rule -> NULL; UNKNOWN beats a fabricated level."""
    assert derive_degree_type(title) is None


def test_doctoral_level_wins_when_a_title_names_two_levels() -> None:
    """Ordered most-specific first: M.Sc. + PhD in one title classifies as PHD."""
    assert derive_degree_type("M.Sc. and PhD in Quantum Computing") == "PHD"


def test_derivation_is_deterministic() -> None:
    title = "M.Sc. Renewable Energy Systems - Technical University"
    assert derive_degree_type(title) == derive_degree_type(title) == "MASTERS"
