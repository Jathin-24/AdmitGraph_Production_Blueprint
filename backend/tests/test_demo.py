"""Demo example & replay unit tests (no database): fixture validity + persona.

The fixture itself is the guarantee that the demo replays REAL data: these
tests pin its provenance (captured run id, counts, per-claim sources) and the
persona's "fill empty fields only" contract from MASTER_SPEC §18.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.core.errors import AppError
from app.db.models import ProfilePreference, StudentProfile
from app.services.demo.fixture import FIXTURE_PATH, load_fixture
from app.services.demo.persona import (
    persona_preference_updates,
    persona_profile_updates,
)
from app.services.demo.runner import DEMO_STEPS

EXPECTED_STEPS = [
    ("validate_profile", "profile"),
    ("plan_queries", "planner"),
    ("discovery_search", "serpapi"),
    ("normalize_programs", "discovery"),
    ("extract_evidence", "evidence"),
    ("evaluate_requirements", "matching"),
    ("score_fit", "scoring"),
    ("assess_risks", "risk"),
    ("build_strategy", "strategy"),
]


def test_captured_fixture_exists_and_holds_the_real_run() -> None:
    fixture = load_fixture()
    assert FIXTURE_PATH.exists()
    # Captured from the real completed run — nothing invented. (Re-capture
    # updates these pins together with this assertion.)
    assert fixture.meta.source_run_id == "0bdefbff-117d-4435-b8a6-144fe28ee83d"
    assert "real" in fixture.meta.note.lower()
    assert len(fixture.institutions) == 9
    assert len(fixture.programs) == 11
    assert len(fixture.sources) == 348
    assert len(fixture.evidence) == 82
    assert len(fixture.requirements) >= 1
    assert fixture.planned_queries, "captured run must keep its planned queries"


def test_every_captured_claim_carries_source_provenance() -> None:
    fixture = load_fixture()
    source_urls = {s.canonical_url for s in fixture.sources}
    for claim in fixture.evidence:
        assert claim.claim
        assert claim.source_url and claim.source_domain
        assert claim.source_url in source_urls, "claim must cite a captured source"
        assert claim.retrieved_at
        assert claim.freshness_deadline


def test_missing_fixture_file_raises_clear_app_error(tmp_path: Path) -> None:
    with pytest.raises(AppError) as exc:
        load_fixture(tmp_path / "absent.json")
    assert exc.value.status_code == 503
    assert exc.value.code == "DEMO_FIXTURE_MISSING"


def test_invalid_fixture_json_raises_clear_app_error(tmp_path: Path) -> None:
    bad = tmp_path / "example.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(AppError) as exc:
        load_fixture(bad)
    assert exc.value.status_code == 503
    assert exc.value.code == "DEMO_FIXTURE_INVALID"


def test_schema_invalid_fixture_raises_clear_app_error(tmp_path: Path) -> None:
    bad = tmp_path / "example.json"
    bad.write_text('{"meta": {}}', encoding="utf-8")
    with pytest.raises(AppError) as exc:
        load_fixture(bad)
    assert exc.value.status_code == 503
    assert exc.value.code == "DEMO_FIXTURE_INVALID"
    assert "validation" in str(exc.value.detail).lower()


def test_demo_steps_mirror_the_live_orchestrator() -> None:
    assert DEMO_STEPS == EXPECTED_STEPS


def test_persona_fills_only_empty_profile_fields() -> None:
    profile = StudentProfile(
        current_degree="B.Tech Electrical Engineering",  # user-provided: untouched
        career_goal="Founder",  # user-provided: untouched
        cgpa=Decimal("9.2"),  # user-provided: untouched
        total_experience_months=0,  # 0 = never provided
    )
    updates = persona_profile_updates(profile)
    assert "current_degree" not in updates
    assert "career_goal" not in updates
    assert "cgpa" not in updates
    assert updates["field_of_study"] == "Artificial Intelligence"
    assert updates["cgpa_scale"] == Decimal("10")
    assert updates["total_budget_amount"] == Decimal("1800000")
    assert updates["budget_currency"] == "INR"
    assert updates["total_experience_months"] == 12


def test_persona_leaves_a_complete_profile_untouched() -> None:
    profile = StudentProfile(
        current_degree="B.Tech Computer Science",
        field_of_study="Artificial Intelligence",
        cgpa=Decimal("8.1"),
        cgpa_scale=Decimal("10"),
        total_budget_amount=Decimal("1800000"),
        budget_currency="INR",
        career_goal="Machine learning engineer",
        total_experience_months=12,
    )
    assert persona_profile_updates(profile) == {}


def test_persona_fills_only_empty_preference_fields() -> None:
    prefs = ProfilePreference(
        preferred_countries=["FR"],  # user-provided: untouched
        preferred_fields=[],
        target_intakes=[],
    )
    updates = persona_preference_updates(prefs)
    assert "preferred_countries" not in updates
    assert updates["preferred_fields"] == ["Artificial Intelligence", "Computer Science"]
    assert updates["target_intakes"] == ["Winter 2027"]

    full = ProfilePreference(
        preferred_countries=["DE"],
        preferred_fields=["Artificial Intelligence"],
        target_intakes=["Winter 2027"],
    )
    assert persona_preference_updates(full) == {}
