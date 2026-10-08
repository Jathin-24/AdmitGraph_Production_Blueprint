from __future__ import annotations

import hashlib
import re
from datetime import UTC, date, datetime, time
from decimal import Decimal
from email.utils import parsedate_to_datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ConfidenceLevel,
    Evidence,
    EvidenceStatus,
    Intake,
    Program,
    SearchResult,
    SearchRun,
    Source,
)
from app.services.evidence.conflicts import _normalize_value
from app.services.evidence.freshness import freshness_deadline

EXTRACTION_VERSION = "v1"

# Program columns the extraction schema may populate. Only non-null extraction
# fields are ever written; anything UNKNOWN stays untouched.
PROGRAM_PROFILE_FIELDS = (
    "country_code",
    "degree_type",
    "field_of_study",
    "official_url",
    "language",
    "duration_months",
    "tuition_amount",
    "tuition_currency",
)

# Minimum claim confidence required before extracted program facts are persisted.
PROFILE_ENRICHMENT_MIN_CONFIDENCE = ("HIGH", "MEDIUM")


class ExtractedClaim(BaseModel):
    claim_type: str
    normalized_key: str
    value: dict[str, Any] = {}
    mandatory: bool = True
    claim: str
    subject_type: str = "program"
    subject_id: UUID | None = None
    confidence: ConfidenceLevel = ConfidenceLevel.LOW


class ExtractedClaims(BaseModel):
    claims: list[ExtractedClaim] = Field(default_factory=list)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# ---------------------------------------------------------------- published_at
# Publication dates are only recorded when the stored search result actually
# carries one. Anything that cannot be parsed into a real point in time
# (relative phrases such as "3 hours ago", prose dates without a publication
# label, bare numbers) stays UNKNOWN - `published_at` remains NULL. A deadline
# mentioned in a snippet is never mistaken for the page's publication date.

# Provider fields that hold a publication timestamp when present.
_PUBLICATION_KEYS = ("published_at", "published_date", "publication_date", "date_published", "date")
# `detected_extensions` (google_jobs / google_news) nests the same fields.
_EXTENSION_PUBLICATION_KEYS = ("published_at", "posted_at", "published_date", "date")

_MONTHS: dict[str, int] = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sept": 9, "sep": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}
_MONTH_PATTERN = "|".join(sorted(_MONTHS, key=len, reverse=True))
_HUMAN_MDY = re.compile(
    rf"^(?P<mon>{_MONTH_PATTERN})\.?\s+(?P<day>\d{{1,2}}),?\s+(?P<year>\d{{4}})$", re.IGNORECASE
)
_HUMAN_DMY = re.compile(
    rf"^(?P<day>\d{{1,2}})\s+(?P<mon>{_MONTH_PATTERN})\.?\s+(?P<year>\d{{4}})$", re.IGNORECASE
)
# A date in prose counts as a publication date only next to an explicit label.
_PUBLICATION_LABEL = re.compile(
    r"\b(?:published|updated|posted|dated|issued)\b[^0-9]{0,40}?"
    r"(?P<date>\d{4}-\d{2}-\d{2}|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}|\d{1,2}\s+[A-Za-z]{3,9}\.?\s+\d{4})",
    re.IGNORECASE,
)


def _human_date(text: str) -> datetime | None:
    """`Jan 15, 2026` / `15 January 2026` (English only) -> aware datetime."""
    match = _HUMAN_MDY.match(text) or _HUMAN_DMY.match(text)
    if match is None:
        return None
    groups = match.groupdict()
    month = _MONTHS.get(str(groups["mon"]).lower())
    if month is None:
        return None
    try:
        return datetime(int(groups["year"]), month, int(groups["day"]), tzinfo=UTC)
    except ValueError:  # e.g. "Feb 30, 2026" - not a real date, stays UNKNOWN
        return None


def parse_date_value(raw: Any) -> datetime | None:
    """Strictly convert a stored provider value into an aware datetime.

    Accepts real date/datetime objects, unix timestamps (s or ms) and the
    ISO 8601 / RFC 2822 / English calendar formats. Everything else - most
    importantly relative phrases like "3 hours ago" - returns None.
    """
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo is not None else raw.replace(tzinfo=UTC)
    if isinstance(raw, date):
        return datetime.combine(raw, time.min, tzinfo=UTC)
    if isinstance(raw, (int, float)):
        seconds = float(raw)
        if seconds > 1e12:  # millisecond epoch
            seconds /= 1000.0
        # Plausible epoch window (1973..2096): anything else is not a date.
        if not 1e8 <= seconds <= 4e9:
            return None
        return datetime.fromtimestamp(seconds, tz=UTC)
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        pass
    else:
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    try:
        parsed = parsedate_to_datetime(text)  # RFC 2822: "Tue, 15 Jan 2026 ..."
    except (TypeError, ValueError):
        pass
    else:
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return _human_date(text)


def extract_published_at(search_result: SearchResult) -> datetime | None:
    """Publication timestamp of a stored result, or None when not genuinely present.

    Sources, in order: explicitly named `published_*` payload fields, the
    payload's `date` field, `detected_extensions` (google_jobs/google_news),
    then a publication-labelled date in the title/snippet. Unlabelled prose
    dates (application deadlines!) are deliberately ignored.
    """
    payload = search_result.raw_payload
    if isinstance(payload, dict):
        for key in _PUBLICATION_KEYS:
            found = parse_date_value(payload.get(key))
            if found is not None:
                return found
        extensions = payload.get("detected_extensions")
        if isinstance(extensions, dict):
            for key in _EXTENSION_PUBLICATION_KEYS:
                found = parse_date_value(extensions.get(key))
                if found is not None:
                    return found
    for text in (search_result.title, search_result.snippet):
        if not text:
            continue
        labelled = _PUBLICATION_LABEL.search(text)
        if labelled is not None:
            found = parse_date_value(labelled.group("date"))
            if found is not None:
                return found
    return None


async def safe_program_subject(
    session: AsyncSession, *, title: str | None, source: Source | None
) -> UUID | None:
    """Bind a stored result to a program only when the match is unambiguous.

    Two safe rules (the same ones the research orchestrator applies to search
    results): (1) an exact normalized program-title match that resolves to
    exactly one program, or (2) a source domain that belongs to catalog
    institutions owning exactly one program in total. Anything ambiguous or
    unmatched returns None - the subject stays NULL instead of a guess.
    """
    if title:
        normalized = title.split(" - ")[0].strip().lower()
        if normalized:
            matches = (
                (await session.execute(select(Program).where(Program.normalized_name == normalized).limit(2)))
                .scalars()
                .all()
            )
            if len(matches) == 1:
                return matches[0].id
    if source is None or not source.domain:
        return None
    # Institutions store heterogeneous domains (program root, subdomains, www.),
    # so registrable suffixes are matched - never a naive string comparison.
    candidates: set[str] = set()
    parts = source.domain.lower().split(".")
    for i in range(len(parts) - 1):
        suffix = ".".join(parts[i:])
        candidates.add(suffix)
        candidates.add("www." + suffix)
    from app.db.models import Institution

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
    # Several programs behind one domain are ambiguous: leave the claim unbound.
    return rows[0].id if len(rows) == 1 else None


async def load_signal_rows(
    session: AsyncSession,
    *,
    purposes: tuple[str, ...],
    engines: tuple[str, ...],
    search_run_ids: list[UUID] | None = None,
    limit: int = 20,
) -> list[tuple[SearchResult, Source, SearchRun]]:
    """Stored (result, source, run) rows for a signal purpose — never a live search.

    Results without a source are dropped (Evidence always cites one). The
    window is bounded and ordered newest-first so recording a signal is a
    bounded, idempotent pass over rows that already exist; a failed or
    circuit-open run simply has no rows and yields an honest zero.
    """
    filters: list[Any] = []
    if purposes:
        filters.append(SearchRun.parameters["purpose"].astext.in_(purposes))
    if engines:
        filters.append(SearchRun.engine.in_(engines))
    if not filters:
        return []
    stmt = (
        select(SearchResult, Source, SearchRun)
        .join(SearchRun, SearchResult.search_run_id == SearchRun.id)
        .join(Source, SearchResult.source_id == Source.id, isouter=True)
        .where(or_(*filters))
        .order_by(SearchRun.requested_at.desc(), SearchResult.position.asc().nulls_last())
        .limit(limit)
    )
    if search_run_ids:
        stmt = stmt.where(SearchRun.id.in_(search_run_ids))
    rows = (await session.execute(stmt)).all()
    return [(result, source, run) for result, source, run in rows if source is not None]


class EvidenceExtractionService:
    """Converts normalized search results into Evidence rows with full provenance.

    LLM extraction is optional; every claim must cite a search result (evidence_id
    in the provider output is accepted only if it matches a stored result).
    """

    async def record_claims(
        self,
        session: AsyncSession,
        source: Source,
        search_result: SearchResult,
        claims: list[ExtractedClaim],
        *,
        extraction_model: str | None = None,
    ) -> list[Evidence]:
        """Store one Evidence row per claim (provenance always cited).

        De-duplication: a claim re-observed for the SAME search result with the
        SAME normalized key and the IDENTICAL extracted value is the same claim,
        not a new one - the existing row is refreshed (retrieved_at and
        freshness_deadline recomputed from the stored result: honest freshness,
        no invented recency) and returned in place of a duplicate insert, which
        otherwise grows evidence/conflict tables without bound every run.
        Rows whose extracted_value differs are still inserted: differing values
        are conflicts, which are a feature, not noise.

        Returns the Evidence rows in input order (refreshed rows included), so
        callers can keep pairing claims with their stored rows positionally.
        """
        stored: list[Evidence] = []
        for claim in claims:
            existing = await self._find_reobservation(session, search_result, claim)
            if existing is not None:
                existing.retrieved_at = search_result.retrieved_at
                existing.freshness_deadline = freshness_deadline(
                    claim.claim_type, search_result.retrieved_at
                )
                # Re-observed inside the window: CURRENT again. CONFLICTING is
                # never cleared here - the user still has to verify it.
                if existing.status == EvidenceStatus.STALE:
                    existing.status = EvidenceStatus.CURRENT
                stored.append(existing)
                continue
            evidence = Evidence(
                source_id=source.id,
                search_result_id=search_result.id,
                claim_type=claim.claim_type,
                subject_type=claim.subject_type,
                subject_id=claim.subject_id,
                claim=claim.claim,
                normalized_claim=claim.normalized_key,
                snippet=search_result.snippet,
                extracted_value=claim.value,
                authority_score=source.authority_score if hasattr(source, "authority_score") else 0,
                confidence=claim.confidence,
                status=EvidenceStatus.CURRENT,
                retrieved_at=search_result.retrieved_at,
                published_at=extract_published_at(search_result),
                freshness_deadline=freshness_deadline(claim.claim_type, search_result.retrieved_at),
                content_hash=content_hash((search_result.snippet or "") + claim.claim),
                extraction_model=extraction_model,
                extraction_version=EXTRACTION_VERSION,
            )
            session.add(evidence)
            await session.flush()  # id + visibility for the duplicate lookup below
            stored.append(evidence)
        await session.flush()
        return stored

    async def _find_reobservation(
        self, session: AsyncSession, search_result: SearchResult, claim: ExtractedClaim
    ) -> Evidence | None:
        """Existing row for (same search result, same key, same value), if any."""
        stmt = select(Evidence).where(Evidence.normalized_claim == claim.normalized_key)
        if search_result.id is None:
            stmt = stmt.where(Evidence.search_result_id.is_(None))
        else:
            stmt = stmt.where(Evidence.search_result_id == search_result.id)
        wanted = _normalize_value(dict(claim.value))
        for row in (await session.execute(stmt)).scalars().all():
            if _normalize_value(dict(row.extracted_value or {})) == wanted:
                return row
        return None

    async def claims_for_program(self, session: AsyncSession, program_id: UUID) -> list[Evidence]:
        result = await session.execute(
            select(Evidence).where(Evidence.subject_type == "program", Evidence.subject_id == program_id)
        )
        return list(result.scalars().all())


def _quarter_label(value: date) -> str:
    """Cosmetic quarter label for an intake row (never a start-date claim)."""
    if value.month in (12, 1, 2):
        return "Winter"
    if value.month in (3, 4, 5):
        return "Spring"
    if value.month in (6, 7, 8):
        return "Summer"
    return "Fall"


async def _country_exists(session: AsyncSession, code: str | None) -> str | None:
    """Return an upper-cased code only when it exists in `countries` (FK-safe)."""
    from app.db.models import Country

    if not code or len(code.strip()) != 2:
        return None
    code = code.strip().upper()
    found = (await session.execute(select(Country.code).where(Country.code == code))).first()
    return code if found is not None else None


async def apply_program_profile(
    session: AsyncSession,
    program: Program,
    profile: dict[str, Any],
    *,
    confidence: str,
) -> dict[str, Any]:
    """Upsert ONLY non-null, sufficiently-confident extracted fields onto Program.

    Missing/None fields are UNKNOWN and are never written (no invented facts).
    Returns a summary of which fields changed.
    """
    if confidence.upper() not in PROFILE_ENRICHMENT_MIN_CONFIDENCE:
        return {"updated_fields": []}
    updated: list[str] = []
    for field_name in PROGRAM_PROFILE_FIELDS:
        if field_name not in profile:
            continue
        value = profile.get(field_name)
        if value is None or value == "":
            continue
        if field_name == "country_code":
            safe = await _country_exists(session, str(value))
            if safe is None:
                continue
            value = safe
        elif field_name == "tuition_amount":
            try:
                value = Decimal(str(value)).quantize(Decimal("0.01"))
            except Exception:  # noqa: BLE001 - malformed figure stays UNKNOWN
                continue
        elif field_name == "duration_months":
            try:
                value = int(value)
            except (TypeError, ValueError):
                continue
        elif field_name == "official_url":
            value = str(value)
            if not value.startswith(("http://", "https://")):
                continue
        else:
            value = str(value)
        setattr(program, field_name, value)
        updated.append(field_name)
    if updated:
        program.last_verified_at = datetime.now(UTC)
    await session.flush()
    return {"updated_fields": updated}


async def upsert_intake_from_deadline(
    session: AsyncSession,
    *,
    program_id: UUID,
    deadline: date,
    intake_year: int,
    evidence_id: UUID | None = None,
) -> tuple[Intake, bool]:
    """Idempotent intake upsert keyed by (program, intake_year).

    The label combines a quarter with the run's intake year ("Winter 2027" style);
    it is a cosmetic grouping, not a claim about the program's start date.
    """
    existing = (
        await session.execute(
            select(Intake).where(Intake.program_id == program_id, Intake.intake_year == intake_year)
        )
    ).scalars().first()
    label = f"{_quarter_label(deadline)} {intake_year}"
    if existing is not None:
        existing.application_deadline = deadline
        existing.deadline_type = existing.deadline_type or "application"
        if evidence_id is not None:
            existing.evidence_id = evidence_id
        await session.flush()
        return existing, False
    intake = Intake(
        program_id=program_id,
        intake_label=label,
        intake_year=intake_year,
        application_deadline=deadline,
        deadline_type="application",
        evidence_id=evidence_id,
    )
    session.add(intake)
    await session.flush()
    return intake, True


def parse_deadline_date(value: dict[str, Any]) -> date | None:
    """Extract an ISO date from a deadline claim value; None when not a date.

    Accepts `date` (single deadline), `end` (application-window close — the
    effective deadline) and `deadline`. Prose or term/year-only values stay
    UNKNOWN; no date is ever invented.
    """
    for key in ("date", "end", "deadline"):
        raw = value.get(key)
        if raw is None:
            continue
        try:
            return date.fromisoformat(str(raw)[:10])
        except (TypeError, ValueError):
            continue
    return None
