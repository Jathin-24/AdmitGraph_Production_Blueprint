from datetime import date, timedelta
from decimal import Decimal

from app.db.models import RequirementStatus
from app.services.matching.evaluator import ProfileFacts, evaluate
from app.services.matching.normalize import (
    is_test_expired,
    normalize_cgpa_to_percentage,
    normalize_percentage_to_cgpa,
)
from app.services.scoring.scorer import DimensionInput, overall_score


def test_cgpa_normalization() -> None:
    assert normalize_cgpa_to_percentage(Decimal("8.1"), Decimal(10)) == Decimal(81)


def test_cgpa_cross_scale_comparison() -> None:
    # 3.0/4.0 = 75%: 8.1/10 (81%) passes, 7.0/10 (70%) does not.
    ok, _ = evaluate(
        {"normalized_key": "cgpa_min", "value": {"min": 3.0, "scale": "4.0"}},
        ProfileFacts(cgpa=Decimal("8.1"), cgpa_scale=Decimal(10)),
    )
    assert ok == RequirementStatus.SATISFIED
    fail, reason = evaluate(
        {"normalized_key": "cgpa_min", "value": {"min": 3.0, "scale": "4.0"}},
        ProfileFacts(cgpa=Decimal("7.0"), cgpa_scale=Decimal(10)),
    )
    assert fail == RequirementStatus.NOT_SATISFIED
    assert "normalized" in reason


def test_cgpa_same_scale_unchanged() -> None:
    status, _ = evaluate(
        {"normalized_key": "cgpa_min", "value": {"min": 3.5}},
        ProfileFacts(cgpa=Decimal("3.2")),
    )
    assert status == RequirementStatus.NOT_SATISFIED


def test_missing_ielts_is_unknown_not_guessed() -> None:
    status, _ = evaluate({"normalized_key": "ielts_overall_min", "value": {"min": 6.5}}, ProfileFacts())
    assert status == RequirementStatus.UNKNOWN


def test_ielts_threshold_partial() -> None:
    status, _ = evaluate(
        {"normalized_key": "ielts_overall_min", "value": {"min": 7.0}},
        ProfileFacts(ielts_overall=Decimal("6.5")),
    )
    assert status == RequirementStatus.PARTIAL


def test_expired_test() -> None:
    assert is_test_expired(date(2020, 1, 1)) is True
    assert is_test_expired(None) is False


def test_prerequisite_subjects_missing() -> None:
    status, reason = evaluate(
        {"normalized_key": "prerequisite_subjects", "value": {"subjects": ["math", "physics"]}},
        ProfileFacts(subjects={"math"}),
    )
    assert status == RequirementStatus.PARTIAL
    assert "physics" in reason


def test_budget_threshold() -> None:
    status, _ = evaluate(
        {"normalized_key": "budget_min", "value": {"amount": "2000000"}},
        ProfileFacts(total_budget_amount=Decimal("1500000")),
    )
    assert status == RequirementStatus.NOT_SATISFIED


def test_deadline_passed() -> None:
    status, _ = evaluate(
        {"normalized_key": "application_deadline", "value": {"date": "2020-01-01"}}, ProfileFacts()
    )
    assert status == RequirementStatus.NOT_SATISFIED


def test_overall_score_weights() -> None:
    score, subs = overall_score(
        [
            DimensionInput("academic", [RequirementStatus.SATISFIED]),
            DimensionInput("language", [RequirementStatus.UNKNOWN]),
        ]
    )
    assert subs["academic"] == Decimal(100)
    assert Decimal(0) < score < Decimal(100)


# ---------------------------------------------------------------- expiry (#4)


def test_expired_test_is_never_satisfied() -> None:
    # Even a score that clears the bar fails once the certificate expired.
    status, reason = evaluate(
        {"normalized_key": "ielts_overall_min", "value": {"min": 6.5}},
        ProfileFacts(ielts_overall=Decimal("7.5"), language_expiry_date=date(2020, 1, 1)),
    )
    assert status == RequirementStatus.NOT_SATISFIED
    assert reason == "test expired 2020-01-01"


def test_expiry_is_checked_before_the_missing_score() -> None:
    # Expiry outranks UNKNOWN: an expired certificate with no usable score is
    # a definite fail, not "we don't know".
    status, reason = evaluate(
        {"normalized_key": "ielts_overall_min", "value": {"min": 6.5}},
        ProfileFacts(english_test_type="IELTS", language_expiry_date=date(2020, 1, 1)),
    )
    assert status == RequirementStatus.NOT_SATISFIED
    assert "expired" in reason


def test_unexpired_test_keeps_score_evaluation() -> None:
    status, _ = evaluate(
        {"normalized_key": "ielts_overall_min", "value": {"min": 6.5}},
        ProfileFacts(ielts_overall=Decimal("7.5"), language_expiry_date=date(2030, 1, 1)),
    )
    assert status == RequirementStatus.SATISFIED


def test_post_construction_facts_are_coerced_at_read_time() -> None:
    # Mirrors persist._profile_facts: plain attribute assignment (even with an
    # ISO string) bypasses __post_init__, so evaluate() must still read it.
    facts = ProfileFacts(ielts_overall=Decimal("7.5"))
    facts.language_expiry_date = "2020-01-01"  # type: ignore[assignment]
    status, _ = evaluate(
        {"normalized_key": "ielts_overall_min", "value": {"min": 6.5}}, facts
    )
    assert status == RequirementStatus.NOT_SATISFIED


# ------------------------------------------------ non-IELTS reported type (#5)


def test_reported_non_ielts_needs_verification_never_conversion() -> None:
    for reported in ("TOEFL", "PTE", "DET", "english_overall"):
        status, reason = evaluate(
            {"normalized_key": "ielts_overall_min", "value": {"min": 6.5}},
            ProfileFacts(english_test_type=reported),
        )
        assert status == RequirementStatus.NEEDS_VERIFICATION, reported
        assert reason == f"reported {reported}; equivalence to IELTS not asserted"


def test_ielts_type_without_score_stays_unknown() -> None:
    # IELTS reported but no number on file: UNKNOWN, never a guessed score.
    status, _ = evaluate(
        {"normalized_key": "ielts_overall_min", "value": {"min": 6.5}},
        ProfileFacts(english_test_type="IELTS"),
    )
    assert status == RequirementStatus.UNKNOWN


def test_language_score_never_leaks_into_the_ielts_comparison() -> None:
    # A TOEFL-scale number (96) must not be compared as if it were IELTS bands.
    facts = ProfileFacts(english_test_type="TOEFL")
    facts.language_score = Decimal("96")
    status, _ = evaluate(
        {"normalized_key": "ielts_overall_min", "value": {"min": 6.5}}, facts
    )
    assert status == RequirementStatus.NEEDS_VERIFICATION


# ------------------------------------------------------------- credits (TEST_PLAN)


def test_credits_from_profile_fact_satisfy_requirement() -> None:
    status, _ = evaluate(
        {"normalized_key": "min_credits", "value": {"credits": 120}},
        ProfileFacts(matched_credits=Decimal("180")),
    )
    assert status == RequirementStatus.SATISFIED


def test_credits_below_threshold_fail() -> None:
    status, reason = evaluate(
        {"normalized_key": "min_credits", "value": {"credits": 120}},
        ProfileFacts(matched_credits=Decimal("90")),
    )
    assert status == RequirementStatus.NOT_SATISFIED
    assert "90" in reason and "120" in reason


def test_credits_unknown_when_no_fact_anywhere() -> None:
    status, _ = evaluate({"normalized_key": "min_credits", "value": {"credits": 120}}, ProfileFacts())
    assert status == RequirementStatus.UNKNOWN


def test_credits_fall_back_to_injected_value_when_fact_missing() -> None:
    status, _ = evaluate(
        {"normalized_key": "min_credits", "value": {"credits": 120, "_matched_credits": 120}},
        ProfileFacts(),
    )
    assert status == RequirementStatus.SATISFIED


def test_profile_credits_win_over_the_injected_value() -> None:
    # Conflicting inputs: the profile fact is authoritative (90 < 120 fails,
    # even though the injected hint claims 150).
    status, _ = evaluate(
        {"normalized_key": "min_credits", "value": {"credits": 120, "_matched_credits": 150}},
        ProfileFacts(matched_credits=Decimal("90")),
    )
    assert status == RequirementStatus.NOT_SATISFIED


# ------------------------------------------- percentage normalization (TEST_PLAN)


def test_percentage_round_trips_through_cgpa() -> None:
    cgpa = Decimal("8.1")
    percentage = normalize_cgpa_to_percentage(cgpa, Decimal(10))
    assert percentage == Decimal(81)
    assert normalize_percentage_to_cgpa(percentage, Decimal(10)) == cgpa


def test_percentage_bounds_and_degenerate_scales() -> None:
    assert normalize_percentage_to_cgpa(Decimal(0), Decimal(4)) == Decimal(0)
    assert normalize_percentage_to_cgpa(Decimal(100), Decimal(4)) == Decimal(4)
    assert normalize_percentage_to_cgpa(Decimal("-0.1"), Decimal(10)) is None
    assert normalize_percentage_to_cgpa(Decimal("100.1"), Decimal(10)) is None
    # A non-positive scale has no defined conversion (mirrors the sibling helper).
    assert normalize_percentage_to_cgpa(Decimal(50), Decimal(0)) is None
    assert normalize_percentage_to_cgpa(Decimal(50), Decimal(-4)) is None


def test_percentage_only_profile_satisfies_cross_scale_requirement() -> None:
    # 81% on a 10-point transcript vs 3.0/4.0 (75%): satisfied without a CGPA.
    status, _ = evaluate(
        {"normalized_key": "academic_cgpa_min", "value": {"min": 3.0, "scale": "4.0"}},
        ProfileFacts(percentage=Decimal("81"), cgpa_scale=Decimal(10)),
    )
    assert status == RequirementStatus.SATISFIED


def test_percentage_only_same_scale_comparison_and_reason() -> None:
    status, reason = evaluate(
        {"normalized_key": "academic_cgpa_min", "value": {"min": 3.5}},
        ProfileFacts(percentage=Decimal("70"), cgpa_scale=Decimal(10)),
    )
    assert status == RequirementStatus.SATISFIED
    assert "derived from percentage" in reason


def test_percentage_only_still_fails_a_missed_threshold() -> None:
    status, reason = evaluate(
        {"normalized_key": "academic_cgpa_min", "value": {"min": 8.0}},
        ProfileFacts(percentage=Decimal("70"), cgpa_scale=Decimal(10)),
    )
    assert status == RequirementStatus.NOT_SATISFIED
    assert "derived from percentage" in reason


def test_percentage_without_any_known_scale_stays_unknown() -> None:
    # No profile scale and no requirement scale: comparing would assume one.
    status, _ = evaluate(
        {"normalized_key": "academic_cgpa_min", "value": {"min": 3.0}},
        ProfileFacts(percentage=Decimal("81")),
    )
    assert status == RequirementStatus.UNKNOWN


def test_cgpa_takes_precedence_over_percentage() -> None:
    status, _ = evaluate(
        {"normalized_key": "academic_cgpa_min", "value": {"min": 3.5}},
        ProfileFacts(cgpa=Decimal("3.0"), percentage=Decimal("99")),
    )
    assert status == RequirementStatus.NOT_SATISFIED


# --------------------------------------------------------- deadline window (#7)


def test_deadline_window_uses_end_as_the_deadline() -> None:
    future_end = (date.today() + timedelta(days=45)).isoformat()
    status, _ = evaluate(
        {
            "normalized_key": "application_deadline",
            "value": {"start": "2026-01-01", "end": future_end},
        },
        ProfileFacts(),
    )
    assert status == RequirementStatus.SATISFIED


def test_deadline_window_with_a_closed_end_fails() -> None:
    status, _ = evaluate(
        {
            "normalized_key": "application_deadline",
            "value": {"start": "2020-01-01", "end": "2020-02-01"},
        },
        ProfileFacts(),
    )
    assert status == RequirementStatus.NOT_SATISFIED


def test_deadline_window_copy_with_date_key_agrees() -> None:
    # persist passes a COPY of the window with "date" = "end" added: both
    # shapes must produce the same verdict.
    future_end = (date.today() + timedelta(days=45)).isoformat()
    window = {"start": "2026-01-01", "end": future_end}
    from_window, _ = evaluate(
        {"normalized_key": "application_deadline", "value": dict(window)}, ProfileFacts()
    )
    from_copy, _ = evaluate(
        {"normalized_key": "application_deadline", "value": {**window, "date": future_end}},
        ProfileFacts(),
    )
    assert from_window == from_copy == RequirementStatus.SATISFIED


def test_deadline_without_any_usable_date_stays_unknown() -> None:
    status, _ = evaluate(
        {"normalized_key": "application_deadline", "value": {"start": "Winter 2027"}},
        ProfileFacts(),
    )
    assert status == RequirementStatus.UNKNOWN


# ---------------------------------------------- prerequisites with subjects (#8)


def test_prerequisite_subjects_satisfied_from_captured_subjects() -> None:
    # Onboarding stores normalized_subject lowercased; matching normalizes
    # both sides, so captured rows satisfy the prerequisite outright.
    status, _ = evaluate(
        {
            "normalized_key": "prerequisite_subjects",
            "value": {"subjects": ["Mathematics", "Physics"]},
        },
        ProfileFacts(subjects={"mathematics", "physics"}),
    )
    assert status == RequirementStatus.SATISFIED
