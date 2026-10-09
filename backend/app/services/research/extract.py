"""
Evidence extraction (W7 split from research.orchestrator).

Bounded purpose-aware selection window over stored search results, LLM
claim extraction -> Evidence rows, requirement sync, intake minting and
search-result -> program binding. Purpose quotas and the confidence
mapping live here next to the code that uses them.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ConfidenceLevel,
    Evidence,
    Program,
    ResearchPlan,
    SearchResult,
    SearchRun,
    Source,
)

if TYPE_CHECKING:
    from app.services.research.runner import ResearchService

logger = logging.getLogger(__name__)


PROGRAM_PURPOSES = ("requirements", "language", "prerequisites", "deadline_tuition")


# Extraction quotas count *bindable* rows only (see
# _select_extraction_results): buckets over-fetch by this factor and trim in
# Python — institution domains and program titles are tiny in-memory sets.
_BINDABLE_OVERFETCH = 4


# LLM claim extraction is bounded to MAX_RESULTS_PER_RUN results per run
# (llm_extract). That budget is split across purposes so a step whose results
# arrive late is not starved: funding/scholarship queries only run once the
# programs are shortlisted (normalize step), and behind hundreds of program
# pages they would otherwise never reach extraction. Shares sum to 10 and are
# relative to the total, so raising MAX_RESULTS_PER_RUN keeps the proportions.
# `None` = every purpose not named above (policy/news/career).
_EXTRACTION_SHARES: tuple[tuple[tuple[str, ...] | None, int], ...] = (
    (PROGRAM_PURPOSES, 6),
    (("discovery",), 2),
    (("funding",), 1),
    (None, 1),
)


def _extraction_quotas(budget: int) -> list[tuple[tuple[str, ...] | None, int]]:
    """Split `budget` results across the purpose buckets (sums to <= budget)."""
    total_shares = sum(share for _purposes, share in _EXTRACTION_SHARES)
    buckets = len(_EXTRACTION_SHARES)
    quotas: list[tuple[tuple[str, ...] | None, int]] = []
    used = 0
    for index, (purposes, share) in enumerate(_EXTRACTION_SHARES):
        want = (budget * share) // total_shares
        if budget >= buckets:
            want = max(1, want)  # every purpose keeps at least one slot
        if index == buckets - 1:
            want = budget - used  # last bucket absorbs rounding
        quota = max(0, min(want, budget - used))
        quotas.append((purposes, quota))
        used += quota
    return quotas


def _to_confidence(raw: str) -> ConfidenceLevel:
    try:
        return ConfidenceLevel(raw.upper())
    except ValueError:
        return ConfidenceLevel.LOW


# ------------------------------------------------------------- evidence
async def select_extraction_results(
    session: AsyncSession, budget: int
) -> list[SearchResult]:
    """Bounded, purpose-aware, bindable-only window (<= `budget` rows).

    Program pages (deadline/tuition/language) keep the largest share,
    discovery the next; funding — the reserved scholarship slot — and
    everything else share the tail. Within a bucket the newest rows win.

    A row enters the window only when a claim from it could carry a
    subject: its source domain belongs to a catalog institution, or its
    title names an existing program. Subject-less claims from unbindable
    hosts (social/listicle/aggregator pages, `site:` results Google
    dropped off-site) would be stored forever without ever feeding
    requirements or scoring, so they never spend LLM budget.
    """
    from app.db.models import Institution

    inst_domains = [
        (d or "").strip().lower().removeprefix("www.")
        for (d,) in (await session.execute(select(Institution.domain))).all()
        if (d or "").strip()
    ]
    program_names = set(
        (await session.execute(select(Program.normalized_name))).scalars().all()
    )

    def bindable(row: SearchResult, domain: str | None) -> bool:
        d = (domain or "").strip().lower().removeprefix("www.")
        if d and any(d == b or d.endswith("." + b) or b.endswith("." + d) for b in inst_domains):
            return True
        if row.title:
            return row.title.split(" - ")[0].strip().lower() in program_names
        return False

    selected: list[SearchResult] = []
    for purposes, quota in _extraction_quotas(budget):
        if quota <= 0:
            continue
        stmt = (
            select(SearchResult)
            .join(SearchRun, SearchResult.search_run_id == SearchRun.id)
            .where(SearchResult.source_id.is_not(None))
        )
        if purposes is None:
            named = [*PROGRAM_PURPOSES, "discovery", "funding"]
            purpose_col = SearchRun.parameters["purpose"].astext
            stmt = stmt.where(
                or_(purpose_col.is_(None), purpose_col.notin_(named)),
            )
        else:
            stmt = stmt.where(SearchRun.parameters["purpose"].astext.in_(list(purposes)))
        # Most recent queries first: SearchResult.id is a random UUID (not
        # monotonic), so recency comes from the search run's timestamp —
        # this run's official-site program pages must win the window.
        rows = (
            await session.execute(
                stmt.order_by(
                    SearchRun.requested_at.desc(),
                    SearchResult.position.asc().nulls_last(),
                ).limit(quota * _BINDABLE_OVERFETCH)
            )
        ).scalars().all()
        source_ids = [r.source_id for r in rows if r.source_id is not None]
        sources: dict[uuid.UUID, Source] = {}
        if source_ids:
            sources = {
                s.id: s
                for s in (
                    await session.execute(select(Source).where(Source.id.in_(source_ids)))
                ).scalars()
            }
        kept = [
            row
            for row in rows
            if bindable(
                row,
                sources[row.source_id].domain if row.source_id in sources else None,
            )
        ][:quota]
        selected.extend(kept)
    return selected


async def upsert_intakes_from_evidence(session: AsyncSession, intake_year: int) -> int:
    """Mint Intake rows from STORED deadline evidence (idempotent).

    Runs on every extract pass: a deadline claimed in an earlier run must
    still produce its intake, instead of depending on the claim happening
    to be re-extracted today. Values without a parseable date (prose,
    term/year only) stay UNKNOWN and mint nothing.
    """
    from app.services.evidence.extraction import parse_deadline_date, upsert_intake_from_deadline

    rows = (
        await session.execute(
            select(Evidence)
            .where(
                Evidence.subject_type == "program",
                Evidence.subject_id.is_not(None),
                Evidence.normalized_claim == "application_deadline",
            )
            # Oldest first: an upsert overwrites, so the freshest claim
            # is applied last and deterministically wins.
            .order_by(Evidence.retrieved_at.asc(), Evidence.id.asc())
        )
    ).scalars().all()

    # intakes.program_id is a FK: skip evidence whose subject never existed
    # or was deleted (evidence.subject_id is intentionally FK-less) rather
    # than poisoning the session with a violation — same rule as the
    # requirements sync.
    subject_ids = {row.subject_id for row in rows if row.subject_id is not None}
    known_program_ids: set[uuid.UUID] = set()
    if subject_ids:
        known_program_ids = set(
            (await session.execute(select(Program.id).where(Program.id.in_(subject_ids)))).scalars()
        )

    created = 0
    for row in rows:
        if row.subject_id is None or row.subject_id not in known_program_ids:
            continue
        deadline = parse_deadline_date(row.extracted_value or {})
        if deadline is None:
            continue
        _intake, was_created = await upsert_intake_from_deadline(
            session,
            program_id=row.subject_id,
            deadline=deadline,
            intake_year=intake_year,
            evidence_id=row.id,
        )
        created += int(was_created)
    return created


async def extract_evidence(
    service: ResearchService,
    session: AsyncSession,
    plan: ResearchPlan,
) -> dict[str, Any]:
    """LLM claim extraction from recent search results -> Evidence rows -> Requirements."""
    from app.services.evidence.extraction import (
        EvidenceExtractionService,
        ExtractedClaim,
        apply_program_profile,
        parse_deadline_date,
        upsert_intake_from_deadline,
    )
    from app.services.evidence.llm_extract import MAX_RESULTS_PER_RUN, extract_claims
    from app.services.evidence.requirements import sync_requirements_from_evidence
    from app.services.llm import get_llm_provider

    provider = get_llm_provider(service._settings)
    intake_year = int(plan.requested_goal.get("intake_year", datetime.now(UTC).year + 1))
    rows = await service._select_extraction_results(session, MAX_RESULTS_PER_RUN)

    extraction = EvidenceExtractionService()
    claims_created = 0
    intakes_written = 0
    profiles_updated = 0
    profiles_failed = 0
    touched_programs: set[uuid.UUID] = set()
    for item in rows:
        source = await session.get(Source, item.source_id)
        if source is None:
            continue
        llm_claims = await extract_claims(
            provider,
            title=item.title,
            snippet=item.snippet,
            domain=source.domain,
        )
        if llm_claims is None or not llm_claims.claims:
            continue
        program = await service._match_program(session, item)
        # Captured before the optional enrichment below: a rolled-back
        # savepoint expires the instance, so any later attribute access
        # would lazily load outside the greenlet (MissingGreenlet).
        program_id = program.id if program is not None else None
        extracted = [
            ExtractedClaim(
                claim_type=c.claim_type,
                normalized_key=c.normalized_key,
                value=c.value,
                claim=c.claim,
                confidence=_to_confidence(c.confidence),
                subject_type="program",
                subject_id=program_id,
            )
            for c in llm_claims.claims
        ]
        stored = await extraction.record_claims(
            session, source, item, extracted, extraction_model=None
        )
        claims_created += len(extracted)
        if program is None or program_id is None:
            continue
        touched_programs.add(program_id)

        # Program profile enrichment: only non-null, sufficiently-confident
        # fields are written; UNKNOWN fields stay untouched.
        profile_obj = llm_claims.program_profile
        if profile_obj is not None:
            try:
                # Savepoint: enrichment is optional, so a rejected write must
                # not poison the session (claims, intakes and the rest of the
                # run keep going; the failure is counted, not hidden).
                async with session.begin_nested():
                    outcome = await apply_program_profile(
                        session,
                        program,
                        profile_obj.model_dump(),
                        confidence=llm_claims.max_confidence,
                    )
            except Exception as exc:  # noqa: BLE001 - optional enrichment
                profiles_failed += 1
                logger.warning("program profile enrichment failed for %s: %r", program_id, exc)
            else:
                profiles_updated += len(outcome.get("updated_fields", []))

        # Deadline claims -> Intake rows (idempotent per program+intake_year).
        for claim, evidence_row in zip(llm_claims.claims, stored, strict=False):
            if claim.normalized_key != "application_deadline":
                continue
            deadline = parse_deadline_date(claim.value)
            if deadline is None:
                continue
            _intake, created_intake = await upsert_intake_from_deadline(
                session,
                program_id=program_id,
                deadline=deadline,
                intake_year=intake_year,
                evidence_id=evidence_row.id,
            )
            if created_intake:
                intakes_written += 1

    synced = await sync_requirements_from_evidence(session)

    # Intakes mint from STORED deadline evidence too: an earlier run's
    # deadline claim still produces its intake today (idempotent), so the
    # counter reflects reality instead of today's re-extraction luck.
    intakes_written += await service._upsert_intakes_from_evidence(session, intake_year)

    # Conflict detection is now wired into the pipeline (was never called):
    # differing values for the same normalized key -> CONFLICTING + downgrade.
    from app.services.evidence.conflicts import ConflictDetectionService

    conflicts_detected = 0
    detector = ConflictDetectionService()
    for program_id in touched_programs:
        conflicts = await detector.detect_for_subject(session, "program", program_id)
        conflicts_detected += len(conflicts)

    from app.services.research.funding import scholarship_summary

    funding_out = await scholarship_summary(session, sorted(touched_programs))
    return {
        "claims_created": claims_created,
        "requirements_synced": synced,
        "results_processed": len(rows),
        "program_profiles_updated": profiles_updated,
        "program_profiles_failed": profiles_failed,
        "intakes_written": intakes_written,
        "conflicts_detected": conflicts_detected,
        "scholarship_claims": funding_out["scholarship_claims"],
    }


async def match_program(session: AsyncSession, item: SearchResult) -> Program | None:
    """Link a search result to the program it was collected for.

    Exact normalized-title match first (the discovery result that created
    the program). Fallback: a result from an institution's own domain
    binds to that institution when it has exactly one program — site:
    queries return page titles that differ from the discovery title.
    """
    from app.db.models import Institution

    if item.title:
        normalized = item.title.split(" - ")[0].strip().lower()
        if normalized:
            result = await session.execute(
                select(Program).where(Program.normalized_name == normalized).limit(1)
            )
            program = result.scalar_one_or_none()
            if program is not None:
                return program
    if item.source_id is None:
        return None
    source = await session.get(Source, item.source_id)
    if source is None or not source.domain:
        return None
    # Institutions are stored with heterogeneous domains (program root,
    # subdomain like www.ai.study.fau.eu, sometimes with www.). Match the
    # source domain's registrable suffixes so official-site subpages bind.
    candidates: set[str] = set()
    parts = source.domain.lower().split(".")
    for i in range(len(parts) - 1):
        suffix = ".".join(parts[i:])
        candidates.add(suffix)
        candidates.add("www." + suffix)
    rows = (
        (
            await session.execute(
                select(Program)
                .join(Institution, Institution.id == Program.institution_id)
                .where(Institution.domain.in_(sorted(candidates)))
                .limit(2)
            )
        )
        .scalars()
        .all()
    )
    # Ambiguous domains (several programs of one institution) stay unbound
    # rather than guessing which page a claim belongs to.
    return rows[0] if len(rows) == 1 else None
