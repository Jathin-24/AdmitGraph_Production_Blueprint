from datetime import date
from decimal import Decimal

from app.db.models import RequirementStatus
from app.services.matching.evaluator import ProfileFacts, evaluate
from app.services.matching.normalize import is_test_expired, normalize_cgpa_to_percentage
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
