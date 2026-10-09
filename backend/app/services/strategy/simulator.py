"""Failure simulator: deterministic counterfactual transformations.

`apply_scenario` rewrites a snapshot of the student's profile (plus the little
bit of strategy context the endpoint places next to it: `top_priority_program_ids`,
`preferred_countries`, ...). It never mutates its input and never touches the
database. `recompute_portfolio` then re-scores the strategy's *known* candidates
under that rewritten snapshot and re-tiers them with the real portfolio helper —
a pure computation that writes no rows.

Scenario semantics
------------------
BUDGET_MINUS_25_PERCENT  25% less money: ``total_budget_amount * 0.75``.
IELTS_LOWERED            one band lower: the English score - 1.0 (floor 0).
                          Uses ``ielts_overall`` when present, otherwise the
                          onboarding English score.
REMOVE_COUNTRY           the first preferred country is dropped and, during
                          regeneration, every program inside that country
                          (recorded as ``dropped_countries``) leaves the
                          candidate pool. An explicit ``modifications`` entry
                          ``{"country": "..."}`` names the country to drop and
                          wins over the preference order. Country names resolve
                          through the countries table; anything we cannot
                          resolve disables the filter — we never drop a program
                          on a guess.
DEADLINE_MISSED          the upcoming deadline slips: programs that publish an
                          application-deadline requirement are scored
                          NOT_SATISFIED on the timing dimension; programs
                          without one stay unknown, so this is not a uniform
                          shift.
CUSTOM                   ``extra`` overrides are merged in verbatim.
TOP_3_REJECTED           your three highest-priority applications come back as
                          rejections. (This branch used to fall through and
                          return the profile unchanged; it now models three
                          simultaneous effects:)
                            1. up to three ``top_priority_program_ids`` move to
                               ``rejected_program_ids`` and leave the candidate
                               pool — those offers are gone;
                            2. ``academic_bar_lift_cgpa = 0.5``: admissions raise
                               the academic minimum by ~0.5 CGPA, so every
                               program with a CGPA minimum compares you against
                               a bar half a point higher;
                            3. ``strict_prerequisites = True``: prerequisite
                               requirements with no subject evidence on your
                               profile become NOT_SATISFIED instead of UNKNOWN
                               — a stricter round reads missing data against you.

Honesty note: a strategy re-tiered from already-scored programs is *not* a new
research run. The summary says so in plain language.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Evidence,
    ProgramCategory,
    Requirement,
    RequirementStatus,
    StrategyRun,
)
from app.db.repositories import simulator as simulator_repo
from app.db.repositories import strategy_pipeline as pipeline
from app.services.matching.evaluator import ProfileFacts, evaluate
from app.services.scoring.scorer import DimensionInput, overall_score
from app.services.strategy.persist import (
    evidence_dimension_statuses,
    portfolio_risk_stats,
    programs_with_passed_deadlines,
)
from app.services.strategy.portfolio import Candidate, build_portfolio

SCENARIOS = {
    "TOP_3_REJECTED",
    "BUDGET_MINUS_25_PERCENT",
    "IELTS_LOWERED",
    "REMOVE_COUNTRY",
    "DEADLINE_MISSED",
    "CUSTOM",
}

# How many of the strategy's own candidates we are willing to re-rank. New
# programs would require a fresh research run; this only re-orders what the
# strategy already knows (scored programs + its current plan).
MAX_CANDIDATES = 24

ACADEMIC_BAR_LIFT = 0.5

_CATEGORY_RANK: dict[ProgramCategory, int] = {
    ProgramCategory.LOWER_RISK: 0,
    ProgramCategory.TARGET: 1,
    ProgramCategory.REACH: 2,
}

# Requirement type -> scoring dimension, mirroring persist.step_score_fit so
# the recomputed baseline lines up with the stored strategy.
_DIMENSION_BY_REQUIREMENT_TYPE: dict[str, str] = {
    "academic": "academic",
    "cgpa": "academic",
    "prerequisite": "prerequisites",
    "subjects": "prerequisites",
    "language": "language",
    "test": "language",
    "tuition": "financial",
    "fees": "financial",
    "budget": "financial",
}


def apply_scenario(
    profile_snapshot: dict[str, Any], scenario: str, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Returns a modified profile snapshot (never mutates the input)."""
    modified = dict(profile_snapshot)
    if scenario == "BUDGET_MINUS_25_PERCENT":
        budget = modified.get("total_budget_amount")
        if budget is not None:
            modified["total_budget_amount"] = float(budget) * 0.75
    elif scenario == "IELTS_LOWERED":
        key = "ielts_overall" if modified.get("ielts_overall") is not None else "english_test_overall"
        score = modified.get(key)
        if score is not None:
            modified[key] = max(0.0, float(score) - 1.0)
    elif scenario == "REMOVE_COUNTRY":
        explicit = str((extra or {}).get("country") or "").strip()
        countries = list(modified.get("preferred_countries") or [])
        if explicit:
            # The caller named the country to drop (API `modifications`), so it
            # wins over the preference list: the scenario removes THAT country
            # even when it is not the first preference.
            modified["dropped_countries"] = [explicit]
            modified["preferred_countries"] = [c for c in countries if str(c).strip() != explicit]
        elif countries:
            modified["preferred_countries"] = countries[1:]
            # Explicit, scenario-owned list: only the country the student just
            # removed is filtered out of the regenerated portfolio.
            modified["dropped_countries"] = [countries[0]]
    elif scenario == "DEADLINE_MISSED":
        modified["assume_deadline_missed"] = True
    elif scenario == "CUSTOM":
        if extra:
            modified.update(extra)
    elif scenario == "TOP_3_REJECTED":
        top_ids = [str(pid) for pid in (modified.get("top_priority_program_ids") or [])][:3]
        if top_ids:
            modified["rejected_program_ids"] = top_ids
        modified["academic_bar_lift_cgpa"] = ACADEMIC_BAR_LIFT
        modified["strict_prerequisites"] = True
    return modified


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, ArithmeticError):
        return None


def _language_score(modified: dict[str, Any]) -> Decimal | None:
    """English score on the IELTS 0-9 band scale, or None when uncomparable."""
    if modified.get("ielts_overall") is not None:
        return _dec(modified.get("ielts_overall"))
    score = _dec(modified.get("english_test_overall"))
    if score is None:
        return None
    kind = str(modified.get("english_test_type") or "").upper()
    # Onboarding stores one overall English score. It maps onto the IELTS-band
    # rule only when the test is IELTS-family or the number fits the band
    # scale — a TOEFL-style 96 is not an IELTS 96, so it stays unknown.
    if kind in ("IELTS", "IELTS_ACADEMIC", "ENGLISH_OVERALL") and score <= 9:
        return score
    return None


def _facts_from(modified: dict[str, Any]) -> ProfileFacts:
    """Build the scorer's facts from the (possibly counterfactual) snapshot."""
    facts = ProfileFacts(
        cgpa=_dec(modified.get("cgpa")),
        cgpa_scale=_dec(modified.get("cgpa_scale")),
        percentage=_dec(modified.get("percentage")),
        ielts_overall=_language_score(modified),
        backlogs=int(modified.get("backlogs") or 0),
        total_budget_amount=_dec(modified.get("total_budget_amount")),
        budget_currency=(
            str(modified["budget_currency"]) if modified.get("budget_currency") else None
        ),
        graduation_year=(
            int(modified["graduation_year"])
            if modified.get("graduation_year") is not None
            else None
        ),
    )
    lift = _dec(modified.get("academic_bar_lift_cgpa"))
    if lift is not None and facts.cgpa is not None:
        facts = replace(facts, cgpa=max(Decimal(0), facts.cgpa - lift))
    return facts


def _is_deadline_requirement(req: Requirement) -> bool:
    return req.normalized_key.strip().lower() == "application_deadline" or (
        req.requirement_type in ("deadline", "application_deadline", "intake_deadline")
    )


def _requirement_status(
    req: Requirement, facts: ProfileFacts, modified: dict[str, Any]
) -> RequirementStatus:
    status, _reason = evaluate(
        {
            "normalized_key": req.normalized_key,
            "operator": req.operator or "",
            "value": req.value,
        },
        facts,
    )
    key = req.normalized_key.strip().lower()
    if (
        key == "prerequisite_subjects"
        and status is RequirementStatus.UNKNOWN
        and modified.get("strict_prerequisites")
    ):
        return RequirementStatus.NOT_SATISFIED
    return status


def _score_program(
    requirements: list[Requirement],
    facts: ProfileFacts,
    modified: dict[str, Any],
    evidence_statuses: dict[str, RequirementStatus],
) -> Decimal:
    """Score one program with the real scorer, wired like persist.step_score_fit.

    `evidence_statuses` carries the career/timing/evidence-confidence inputs
    derived from stored evidence — the same derivation the pipeline persists,
    so a recompute mirrors the stored strategy instead of assuming UNKNOWN.
    """
    by_dimension: dict[str, list[RequirementStatus]] = {}
    timing = evidence_statuses.get("timing", RequirementStatus.UNKNOWN)
    for req in requirements:
        if _is_deadline_requirement(req):
            # Baseline timing comes from the evidence (matching the stored
            # strategy); the scenario may flip it once, and only for programs
            # that actually publish a deadline.
            if modified.get("assume_deadline_missed"):
                timing = RequirementStatus.NOT_SATISFIED
            continue
        dimension = _DIMENSION_BY_REQUIREMENT_TYPE.get(req.requirement_type)
        if dimension is None:
            continue
        by_dimension.setdefault(dimension, []).append(
            _requirement_status(req, facts, modified)
        )
    dims: list[DimensionInput] = [
        DimensionInput("academic", by_dimension.get("academic") or [RequirementStatus.UNKNOWN]),
        DimensionInput(
            "prerequisites", by_dimension.get("prerequisites") or [RequirementStatus.UNKNOWN]
        ),
        DimensionInput("language", by_dimension.get("language") or [RequirementStatus.UNKNOWN]),
        DimensionInput("financial", by_dimension.get("financial") or [RequirementStatus.UNKNOWN]),
        DimensionInput(
            "career", [evidence_statuses.get("career", RequirementStatus.UNKNOWN)]
        ),
        DimensionInput("timing", [timing]),
        DimensionInput(
            "evidence_confidence",
            [evidence_statuses.get("evidence_confidence", RequirementStatus.UNKNOWN)],
        ),
    ]
    score, _subscores = overall_score(dims)
    return score


async def _dropped_country_codes(session: AsyncSession, entries: Any) -> set[str] | None:
    """ISO codes of the countries the scenario removed; None = no filtering.

    A single unrecognised entry disables the filter entirely: guessing at what
    a country name means would silently drop programs.
    """
    if not entries:
        return None
    codes: set[str] = set()
    for entry in entries:
        text = str(entry).strip()
        if not text:
            continue
        if len(text) == 2 and text.isalpha():
            codes.add(text.upper())
            continue
        row = await simulator_repo.country_code_by_name(session, text)
        if row is None:
            return None
        codes.add(row)
    return codes or None


async def recompute_portfolio(
    session: AsyncSession, strategy: StrategyRun, modified: dict[str, Any]
) -> tuple[list[dict[str, Any]], int]:
    """Re-score and re-tier the strategy's known candidates under `modified`.

    Pure computation: only SELECTs run, no rows are written. The candidate pool
    is this strategy's own FitAssessment rows plus its ApplicationPlan rows
    (capped at MAX_CANDIDATES) minus any counterfactually rejected programs and
    minus programs inside a country the scenario removed.

    Returns (portfolio_after rows, number of candidates actually scored).
    """
    fits = await pipeline.strategy_fits(session, strategy.profile_id, strategy.research_plan_id)
    order: list[UUID] = []
    seen: set[UUID] = set()
    for fit in fits:
        if fit.program_id not in seen:
            seen.add(fit.program_id)
            order.append(fit.program_id)
    plan_ids = await simulator_repo.plan_program_ids_for_strategy(session, strategy.id)
    for program_id in plan_ids:
        if program_id not in seen:
            seen.add(program_id)
            order.append(program_id)

    rejected = {str(pid) for pid in (modified.get("rejected_program_ids") or [])}
    candidate_ids = [pid for pid in order if str(pid) not in rejected][:MAX_CANDIDATES]
    if not candidate_ids:
        return [], 0

    programs = await simulator_repo.programs_with_institution(session, candidate_ids)
    by_program = {p.id: p for p in programs}
    requirements = await pipeline.requirements_for_programs(session, set(candidate_ids))
    reqs_by_program: dict[UUID, list[Requirement]] = {}
    for req in requirements:
        reqs_by_program.setdefault(req.program_id, []).append(req)

    facts = _facts_from(modified)
    dropped_codes = await _dropped_country_codes(session, modified.get("dropped_countries"))

    # Same inputs the pipeline persists: evidence-derived dimension statuses,
    # open risks per program, and programs whose deadlines all passed.
    evidence_by_program: dict[UUID, list[Evidence]] = {}
    if candidate_ids:
        for row in await simulator_repo.program_evidence(session, candidate_ids):
            if row.subject_id is not None:
                evidence_by_program.setdefault(row.subject_id, []).append(row)
    passed_deadlines = await programs_with_passed_deadlines(session, set(candidate_ids))
    risk_stats = await portfolio_risk_stats(session, strategy.profile_id)

    candidates: list[Candidate] = []
    for program_id in candidate_ids:
        program = by_program.get(program_id)
        if program is None:
            continue
        if (
            dropped_codes is not None
            and program.country_code is not None
            and program.country_code.upper() in dropped_codes
        ):
            continue
        if program_id in passed_deadlines:
            # TEST_PLAN "Deadline passed -> program excluded": the counterfactual
            # portfolio follows the same rule as the stored one.
            continue
        statuses = evidence_dimension_statuses(evidence_by_program.get(program_id, []))
        score = _score_program(reqs_by_program.get(program_id, []), facts, modified, statuses)
        stats = risk_stats.get(program_id)
        candidates.append(
            Candidate(
                program_id=str(program_id),
                fit_score=score,
                risk_count=stats[0] if stats else 0,
                top_risk_severity=stats[1] if stats else None,
                institution_id=str(program.institution_id),
                country_code=program.country_code,
            )
        )

    if not candidates:
        return [], 0

    portfolio = build_portfolio(candidates)
    rows: list[dict[str, Any]] = []
    for category, items in portfolio.tiers.items():
        for index, cand in enumerate(items):
            program = by_program.get(UUID(cand.program_id))
            if program is None:
                continue
            rows.append(
                {
                    "program_id": cand.program_id,
                    "program_name": program.canonical_name,
                    "institution": (
                        program.institution.canonical_name if program.institution else None
                    ),
                    "category": category.value,
                    "priority": index + 1,
                    "fit_score": str(cand.fit_score),
                }
            )
    rows.sort(key=lambda row: (_category_rank(str(row.get("category"))), int(row["priority"])))
    return rows, len(candidates)


def _category_rank(category: Any) -> int:
    try:
        return _CATEGORY_RANK[ProgramCategory(str(category))]
    except ValueError:
        return 9


def _rank(row: dict[str, Any]) -> tuple[int, int]:
    """Sort key for portfolio rows: best tier first, then priority in tier."""
    return (_category_rank(row.get("category")), int(row.get("priority") or 0))


def compute_delta(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    scenario: str,
    candidates_scored: int,
) -> dict[str, Any]:
    """Diff two portfolios and describe the change in one honest sentence."""
    before_by_id = {str(row.get("program_id")): row for row in before}
    after_by_id = {str(row.get("program_id")): row for row in after}
    added = [row for pid, row in after_by_id.items() if pid not in before_by_id]
    removed = [row for pid, row in before_by_id.items() if pid not in after_by_id]
    moved_up: list[dict[str, Any]] = []
    moved_down: list[dict[str, Any]] = []
    for pid, after_row in after_by_id.items():
        before_row = before_by_id.get(pid)
        if before_row is None:
            continue
        was, now = _rank(before_row), _rank(after_row)
        if now < was:
            moved_up.append(after_row)
        elif now > was:
            moved_down.append(after_row)
    moved_up.sort(key=_rank)
    moved_down.sort(key=_rank)

    changes = len(added) + len(removed) + len(moved_up) + len(moved_down)
    if changes:
        summary = (
            f"{scenario}: re-scored {candidates_scored} known programs under the modified "
            f"profile — {len(added)} added, {len(removed)} removed, {len(moved_up)} moved up, "
            f"{len(moved_down)} moved down."
        )
    else:
        summary = (
            f"{scenario}: re-scored {candidates_scored} known programs under the modified "
            "profile — the portfolio is unchanged."
        )
    return {
        "moved_up": moved_up,
        "moved_down": moved_down,
        "added": added,
        "removed": removed,
        "summary": summary,
    }
