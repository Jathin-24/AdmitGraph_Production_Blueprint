"""Fit scoring persistence (MASTER_SPEC §17 scoring, TEST_PLAN score contract).

`step_score_fit` must store every subscore, a recalculation snapshot of the
inputs it ran against, and the evidence behind each dimension; evidence-derived
statuses come only from stored evidence rows — UNKNOWN when there are none.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.db.models import (
    ConfidenceLevel,
    EligibilityAssessment,
    Evidence,
    EvidenceStatus,
    FitAssessment,
    FitDimensionEvidence,
    Institution,
    Program,
    Requirement,
    RequirementStatus,
)
from app.services.profile import get_or_create_profile
from app.services.scoring.scorer import DEFAULT_WEIGHTS
from app.services.strategy.persist import (
    evidence_dimension_statuses,
    evidence_links_by_dimension,
    step_evaluate_requirements,
    step_score_fit,
)

_NOW = datetime.now(UTC)


# ------------------------------------------------------------ pure derivations


def test_no_evidence_means_unknown_not_a_verdict() -> None:
    statuses = evidence_dimension_statuses([])
    assert statuses == {
        "career": RequirementStatus.UNKNOWN,
        "timing": RequirementStatus.UNKNOWN,
        "evidence_confidence": RequirementStatus.UNKNOWN,
    }


def test_evidence_derives_career_timing_and_confidence() -> None:
    def _evidence(claim_type: str, **overrides: Any) -> Evidence:
        row = Evidence(
            source_id=uuid.uuid4(),
            claim_type=claim_type,
            subject_type="program",
            claim="claim",
            confidence=ConfidenceLevel.HIGH,
            status=EvidenceStatus.CURRENT,
            retrieved_at=_NOW,
            freshness_deadline=_NOW + timedelta(days=30),
        )
        for key, value in overrides.items():
            setattr(row, key, value)
        return row

    # Career/timing rows present (no confidence rule): a signal to re-check.
    statuses = evidence_dimension_statuses(
        [_evidence("career"), _evidence("deadline")]
    )
    assert statuses["career"] is RequirementStatus.NEEDS_VERIFICATION
    assert statuses["timing"] is RequirementStatus.NEEDS_VERIFICATION
    # Every row current + fresh -> evidence confidence is SATISFIED.
    assert statuses["evidence_confidence"] is RequirementStatus.SATISFIED

    # Any CONFLICTING row wins: never silently choose a value.
    conflicted = evidence_dimension_statuses(
        [_evidence("career"), _evidence("career", status=EvidenceStatus.CONFLICTING)]
    )
    assert conflicted["career"] is RequirementStatus.CONFLICTING
    assert conflicted["evidence_confidence"] is RequirementStatus.CONFLICTING

    # A stale row downgrades evidence confidence, but not the career signal.
    stale = evidence_dimension_statuses(
        [_evidence("career", freshness_deadline=_NOW - timedelta(days=1))]
    )
    assert stale["career"] is RequirementStatus.NEEDS_VERIFICATION
    assert stale["evidence_confidence"] is RequirementStatus.NEEDS_VERIFICATION

    # Career is fed ONLY by career claims: a deadline row does not stand in.
    assert evidence_dimension_statuses([_evidence("deadline")])["career"] is (
        RequirementStatus.UNKNOWN
    )


def test_dimension_links_skip_empty_dimensions() -> None:
    career = Evidence(
        source_id=uuid.uuid4(),
        claim_type="career",
        subject_type="program",
        claim="career outcomes",
        confidence=ConfidenceLevel.MEDIUM,
        status=EvidenceStatus.CURRENT,
        retrieved_at=_NOW,
    )
    links = evidence_links_by_dimension([career])
    assert set(links) == {"career", "evidence_confidence"}
    assert links["career"] == [career.id]
    # No timing claim -> no timing link row is fabricated.
    assert "timing" not in links
    assert evidence_links_by_dimension([]) == {}


# ------------------------------------------------------------- DB: score_fit


async def test_score_fit_persists_every_subscore_and_snapshot(db_session: Any) -> None:
    profile = await get_or_create_profile(db_session)
    profile.cgpa = Decimal("8.0")
    profile.cgpa_scale = Decimal("10")
    suffix = uuid.uuid4().hex[:8]

    institution = Institution(
        canonical_name=f"Fit Scoring University {suffix}",
        normalized_name=f"fit scoring university {suffix}",
        domain=f"fit-{suffix}.example.edu",
    )
    db_session.add(institution)
    await db_session.flush()
    program = Program(
        institution_id=institution.id,
        canonical_name=f"M.Sc. Fit Contract {suffix}",
        normalized_name=f"m.sc. fit contract {suffix}",
    )
    db_session.add(program)
    await db_session.flush()

    academic = Requirement(
        program_id=program.id,
        requirement_type="academic",
        title="Minimum CGPA",
        normalized_key="academic_cgpa_min",
        value={"min": 7.6},
        mandatory=True,
        status=RequirementStatus.SATISFIED,
    )
    language = Requirement(
        program_id=program.id,
        requirement_type="language",
        title="IELTS overall",
        normalized_key="language_ielts_overall",
        value={"min": 7.5},
        mandatory=True,
        status=RequirementStatus.NOT_SATISFIED,
    )
    db_session.add_all([academic, language])
    await db_session.flush()

    # Evidence behind two of the dimensions (career + a deadline claim).
    from app.db.models import Source, SourceAuthority

    source = Source(
        url=f"https://fit-{suffix}.example.edu/ai",
        canonical_url=f"https://fit-{suffix}.example.edu/ai-{suffix}",
        domain=f"fit-{suffix}.example.edu",
        title="Fit Scoring University",
        source_authority=SourceAuthority.OFFICIAL_UNIVERSITY,
        last_seen_at=_NOW,
    )
    db_session.add(source)
    await db_session.flush()
    evidence_rows = [
        Evidence(
            source_id=source.id,
            claim_type="career",
            subject_type="program",
            subject_id=program.id,
            claim="Graduates work as data engineers",
            confidence=ConfidenceLevel.HIGH,
            status=EvidenceStatus.CURRENT,
            retrieved_at=_NOW,
            freshness_deadline=_NOW + timedelta(days=60),
        ),
        Evidence(
            source_id=source.id,
            claim_type="deadline",
            subject_type="program",
            subject_id=program.id,
            claim="Applications close 1 March",
            normalized_claim="application_deadline",
            confidence=ConfidenceLevel.HIGH,
            status=EvidenceStatus.CURRENT,
            retrieved_at=_NOW,
            freshness_deadline=_NOW + timedelta(days=60),
        ),
    ]
    db_session.add_all(evidence_rows)
    await db_session.commit()

    result = await step_score_fit(db_session, profile.id, None)
    assert result["fit_assessments_created"] >= 1

    fit = (
        (
            await db_session.execute(
                select(FitAssessment).where(FitAssessment.program_id == program.id)
            )
        )
        .scalars()
        .first()
    )
    assert fit is not None, "the researched program must be scored"

    # Every dimension is stored: SATISFIED academic, failed language, UNKNOWN
    # prerequisites/financial, evidence-derived career/timing/confidence.
    assert fit.academic_score == Decimal("100")
    assert fit.language_score == Decimal("0")
    assert fit.prerequisite_score == Decimal("25")
    assert fit.financial_score == Decimal("25")
    assert fit.career_score == Decimal("25")  # career rows -> NEEDS_VERIFICATION
    assert fit.timing_score == Decimal("25")  # deadline rows -> NEEDS_VERIFICATION
    assert fit.evidence_confidence_score == Decimal("100")  # all rows current + fresh

    # Overall equals the weighted recompute of those very subscores.
    stored = {
        "academic": fit.academic_score,
        "prerequisites": fit.prerequisite_score,
        "language": fit.language_score,
        "financial": fit.financial_score,
        "career": fit.career_score,
        "timing": fit.timing_score,
        "evidence_confidence": fit.evidence_confidence_score,
    }
    total = sum(
        stored[name] * weight for name, weight in DEFAULT_WEIGHTS.items()  # type: ignore[operator]
    )
    assert fit.overall_score == (total / sum(DEFAULT_WEIGHTS.values())).quantize(Decimal("0.01"))

    # The explanation is honest about what a fit score is not.
    assert fit.explanation is not None
    assert "not an admission probability" in fit.explanation

    # Recalculation snapshot: the exact inputs, JSON-serializable as stored.
    snapshot = fit.profile_snapshot
    assert snapshot["scoring_version"] == fit.scoring_version
    assert snapshot["weights"] == {k: str(v) for k, v in DEFAULT_WEIGHTS.items()}
    stored_keys = {req["key"] for req in snapshot["requirements"]}
    assert {"academic_cgpa_min", "language_ielts_overall"} <= stored_keys
    by_key = {req["key"]: req for req in snapshot["requirements"]}
    assert by_key["academic_cgpa_min"]["status"] == "SATISFIED"
    assert by_key["academic_cgpa_min"]["mandatory"] is True
    assert len(snapshot["dimensions"]) == 7
    assert snapshot["dimensions"]["academic"] == ["SATISFIED"]
    assert snapshot["dimensions"]["language"] == ["NOT_SATISFIED"]
    assert snapshot["dimensions"]["career"] == ["NEEDS_VERIFICATION"]
    json.dumps(snapshot)  # must stay JSONB-safe (dates/Decimals already stringified)

    # Evidence provenance per dimension.
    links = (
        (
            await db_session.execute(
                select(FitDimensionEvidence).where(FitDimensionEvidence.fit_assessment_id == fit.id)
            )
        )
        .scalars()
        .all()
    )
    by_dimension: dict[str, int] = {}
    for link in links:
        by_dimension[link.dimension] = by_dimension.get(link.dimension, 0) + 1
    assert by_dimension == {"career": 1, "timing": 1, "evidence_confidence": 2}


# ---------------------------------------------------------- DB: eligibility


async def test_evaluate_writes_eligibility_rows_only_where_a_fit_exists(
    db_session: Any,
) -> None:
    profile = await get_or_create_profile(db_session)
    profile.cgpa = Decimal("8.0")
    profile.cgpa_scale = Decimal("10")
    suffix = uuid.uuid4().hex[:8]

    institution = Institution(
        canonical_name=f"Eligibility University {suffix}",
        normalized_name=f"eligibility university {suffix}",
        domain=f"elig-{suffix}.example.edu",
    )
    db_session.add(institution)
    await db_session.flush()

    def _program(name: str) -> Program:
        program = Program(
            institution_id=institution.id,
            canonical_name=name,
            normalized_name=name.lower(),
        )
        db_session.add(program)
        return program

    scored = _program(f"M.Sc. Eligibility Scored {suffix}")
    unscored = _program(f"M.Sc. Eligibility Unscored {suffix}")
    await db_session.flush()

    scored_req = Requirement(
        program_id=scored.id,
        requirement_type="academic",
        title="Minimum CGPA",
        normalized_key="academic_cgpa_min",
        value={"min": 7.6},
        mandatory=True,
    )
    unscored_req = Requirement(
        program_id=unscored.id,
        requirement_type="academic",
        title="Minimum CGPA",
        normalized_key="academic_cgpa_min",
        value={"min": 7.6},
        mandatory=True,
    )
    db_session.add_all([scored_req, unscored_req])
    await db_session.flush()

    # A fit already exists for `scored` (its program was scored by an earlier
    # run) but not for `unscored`.
    fit = FitAssessment(
        profile_id=profile.id,
        research_plan_id=None,
        program_id=scored.id,
        overall_score=Decimal("70.00"),
        scoring_version="v1",
    )
    db_session.add(fit)
    await db_session.commit()

    result = await step_evaluate_requirements(db_session, profile.id)
    assert result["requirements_evaluated"] >= 2
    assert result["eligibility_assessments_written"] >= 1
    assert result["eligibility_assessments_skipped"] >= 1

    rows = (
        (
            await db_session.execute(
                select(EligibilityAssessment).where(
                    EligibilityAssessment.requirement_id.in_([scored_req.id, unscored_req.id])
                )
            )
        )
        .scalars()
        .all()
    )
    by_requirement = {row.requirement_id: row for row in rows}
    # Written where a fit exists: SATISFIED from profile facts, no evidence
    # behind it -> MEDIUM confidence (never HIGH without evidence).
    written = by_requirement.get(scored_req.id)
    assert written is not None
    assert written.fit_assessment_id == fit.id
    assert written.status is RequirementStatus.SATISFIED
    assert written.confidence is ConfidenceLevel.MEDIUM
    assert written.expected_value == {"min": 7.6}
    assert written.matched_value["cgpa"] == "8.0"
    assert written.reason
    # Skipped (not invented) where no fit exists yet: the first run mints fits
    # in the NEXT step, so there is nothing to hang the row on yet.
    assert scored_req.id in by_requirement
    assert unscored_req.id not in by_requirement

    # Conflicting stored evidence wins over the computed status.
    from app.db.models import Source, SourceAuthority

    source = Source(
        url=f"https://elig-{suffix}.example.edu/ai",
        canonical_url=f"https://elig-{suffix}.example.edu/ai-{suffix}",
        domain=f"elig-{suffix}.example.edu",
        title="Eligibility University",
        source_authority=SourceAuthority.CREDIBLE_SECONDARY,
        last_seen_at=_NOW,
    )
    db_session.add(source)
    await db_session.flush()
    db_session.add(
        Evidence(
            source_id=source.id,
            claim_type="academic",
            subject_type="program",
            subject_id=scored.id,
            claim="Minimum CGPA 9.5",
            normalized_claim="academic_cgpa_min",
            confidence=ConfidenceLevel.MEDIUM,
            status=EvidenceStatus.CONFLICTING,
            retrieved_at=_NOW,
        )
    )
    await db_session.commit()

    await step_evaluate_requirements(db_session, profile.id)
    refreshed = (
        await db_session.execute(
            select(EligibilityAssessment).where(
                EligibilityAssessment.requirement_id == scored_req.id
            )
        )
    ).scalars().one()
    assert refreshed.status is RequirementStatus.CONFLICTING
    # Sources disagree -> LOW confidence, and the row must not claim HIGH.
    assert refreshed.confidence is ConfidenceLevel.LOW
