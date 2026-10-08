"""Risk engine rules (MASTER_SPEC §17 risk assessment, TEST_PLAN §Risks).

Every rule is deterministic: typed risks from requirement keys, provenance via
`requirement_id`/`program_id`, and UNKNOWN treated as unknown — never as a
failure and never as a pass.
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal

from app.db.models import RequirementStatus, RiskSeverity
from app.services.risk.engine import RiskContext, assess


def _by_type(risks: list, risk_type: str) -> list:
    return [r for r in risks if r.risk_type == risk_type]


def test_mandatory_not_satisfied_is_critical_or_high() -> None:
    risks = assess(RiskContext(eligibility=[("language_ielts", RequirementStatus.NOT_SATISFIED, True)]))
    assert any(r.severity in (RiskSeverity.CRITICAL, RiskSeverity.HIGH) for r in risks)


def test_mandatory_prerequisite_and_language_failures_are_critical() -> None:
    risks = assess(
        RiskContext(
            eligibility=[
                ("prerequisite_subjects", RequirementStatus.NOT_SATISFIED, True),
                ("language_ielts_overall", RequirementStatus.NOT_SATISFIED, True),
            ]
        )
    )
    assert {r.risk_type for r in risks} == {"PREREQUISITE", "LANGUAGE"}
    assert all(r.severity is RiskSeverity.CRITICAL for r in risks)
    # Typed titles name the exact requirement, and the action is imperative.
    assert any(r.title == "Requirement not met: prerequisite_subjects" for r in risks)


def test_mandatory_non_typed_failure_is_high_eligibility() -> None:
    risks = assess(RiskContext(eligibility=[("study_level", RequirementStatus.NOT_SATISFIED, True)]))
    typed = _by_type(risks, "ELIGIBILITY")
    assert typed and typed[0].severity is RiskSeverity.HIGH


def test_optional_failure_is_one_step_below_mandatory() -> None:
    risks = assess(
        RiskContext(
            eligibility=[
                ("prerequisite_subjects", RequirementStatus.NOT_SATISFIED, False),
                ("tuition_ceiling", RequirementStatus.NOT_SATISFIED, False),
            ]
        )
    )
    by_key = {r.title: r for r in risks}
    optional_prereq = by_key["Optional requirement not met: prerequisite_subjects"]
    optional_other = by_key["Optional requirement not met: tuition_ceiling"]
    # Typed keys keep their type, and an unmet OPTIONAL bar sits one step
    # below the mandatory ladder: HIGH for prerequisite/language/eligibility,
    # MEDIUM for the rest (here: FINANCIAL).
    assert optional_prereq.risk_type == "PREREQUISITE"
    assert optional_prereq.severity is RiskSeverity.HIGH
    assert optional_other.risk_type == "FINANCIAL"
    assert optional_other.severity is RiskSeverity.MEDIUM


def test_unknown_requirement_medium_data_quality() -> None:
    # An OPTIONAL unknown is a data-quality gap, not an eligibility failure.
    risks = assess(RiskContext(eligibility=[("prerequisites", RequirementStatus.UNKNOWN, False)]))
    assert any(r.risk_type == "DATA_QUALITY" and r.severity == RiskSeverity.MEDIUM for r in risks)


def test_mandatory_unknown_is_a_typed_high_risk_with_a_verify_action() -> None:
    risks = assess(
        RiskContext(
            eligibility=[
                ("language_ielts_overall", RequirementStatus.UNKNOWN, True),
                ("study_level", RequirementStatus.UNKNOWN, True),
            ]
        )
    )
    assert len(risks) == 2
    by_title = {r.title: r for r in risks}
    language = by_title["Requirement unknown: language_ielts_overall"]
    other = by_title["Requirement unknown: study_level"]
    assert language.risk_type == "LANGUAGE"
    assert other.risk_type == "ELIGIBILITY"
    assert all(r.severity is RiskSeverity.HIGH for r in risks)
    # UNKNOWN is not a failure: the action asks for the data, it does not
    # accuse the student of anything.
    assert language.recommended_action.startswith("provide or verify 'language_ielts_overall'")


def test_conflicting_requirement_is_a_high_source_conflict() -> None:
    risks = assess(RiskContext(eligibility=[("application_deadline", RequirementStatus.CONFLICTING, True)]))
    conflict = _by_type(risks, "SOURCE_CONFLICT")
    assert conflict and conflict[0].severity is RiskSeverity.HIGH
    assert "Conflicting information for application_deadline" in conflict[0].title


def test_requirement_id_is_passed_through_as_provenance() -> None:
    requirement_id = uuid.uuid4()
    risks = assess(
        RiskContext(
            eligibility=[("language_ielts_overall", RequirementStatus.NOT_SATISFIED, True, requirement_id)]
        )
    )
    assert risks and risks[0].requirement_id == requirement_id
    # 3-element entries stay valid and simply carry no provenance.
    legacy = assess(RiskContext(eligibility=[("language_ielts", RequirementStatus.NOT_SATISFIED, True)]))
    assert legacy and legacy[0].requirement_id is None


def test_deadline_passed_critical() -> None:
    risks = assess(RiskContext(next_deadline=date.today() - timedelta(days=1)))
    assert any(r.severity == RiskSeverity.CRITICAL and r.risk_type == "DEADLINE" for r in risks)


def test_low_budget_financial_risk() -> None:
    risks = assess(RiskContext(budget=Decimal("1000000"), estimated_cost=Decimal("2000000")))
    financial = [r for r in risks if r.risk_type == "FINANCIAL"]
    assert financial and financial[0].severity == RiskSeverity.HIGH
    # TEST_PLAN "Low budget -> financial risk AND alternatives": the action
    # must name concrete alternatives, not only restate the problem.
    action = financial[0].recommended_action.lower()
    assert "scholarship" in action and "tuition" in action


def test_missing_core_documents_is_one_high_document_risk() -> None:
    risks = assess(RiskContext(missing_documents=["transcript", "language_score"]))
    document = _by_type(risks, "DOCUMENT")
    assert len(document) == 1
    assert document[0].severity is RiskSeverity.HIGH
    assert "transcript" in document[0].reason and "language_score" in document[0].reason
    # Everything present: no document risk is invented.
    assert not _by_type(assess(RiskContext(missing_documents=[])), "DOCUMENT")


def test_language_test_rules_are_tri_state() -> None:
    # Nothing known -> silent (no TEST risk from absence of data).
    assert not _by_type(assess(RiskContext(language_test_present=None)), "TEST")
    # Missing on record -> HIGH with a "provide" action.
    missing = _by_type(assess(RiskContext(language_test_present=False)), "TEST")
    assert missing and missing[0].severity is RiskSeverity.HIGH
    assert missing[0].recommended_action.startswith("provide a language test result")
    # On record without expiry -> validity unknown, MEDIUM verify action.
    unknown = _by_type(
        assess(RiskContext(language_test_present=True, language_test_expiry=None)), "TEST"
    )
    assert unknown and unknown[0].severity is RiskSeverity.MEDIUM
    # Expired -> HIGH, retake before applying.
    expired = _by_type(
        assess(
            RiskContext(
                language_test_present=True,
                language_test_expiry=date.today() - timedelta(days=1),
            )
        ),
        "TEST",
    )
    assert expired and expired[0].severity is RiskSeverity.HIGH
    # Valid with a future expiry -> no risk at all.
    assert not _by_type(
        assess(
            RiskContext(
                language_test_present=True,
                language_test_expiry=date.today() + timedelta(days=300),
            )
        ),
        "TEST",
    )


def test_career_gap_is_one_medium_risk_per_program() -> None:
    program_a, program_b = uuid.uuid4(), uuid.uuid4()
    risks = assess(
        RiskContext(
            programs_without_career_evidence=[(program_a, "M.Sc. A"), (program_b, "M.Sc. B")]
        )
    )
    career = _by_type(risks, "CAREER")
    assert len(career) == 2
    assert {r.program_id for r in career} == {program_a, program_b}
    assert all(r.severity is RiskSeverity.MEDIUM for r in risks)
    assert not _by_type(assess(RiskContext(programs_without_career_evidence=[])), "CAREER")


def test_stale_evidence_medium() -> None:
    risks = assess(RiskContext(stale_evidence_count=3))
    assert any(r.risk_type == "INFORMATION_FRESHNESS" for r in risks)
