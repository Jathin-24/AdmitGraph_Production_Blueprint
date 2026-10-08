from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ConfidenceLevel,
    Evidence,
    EvidenceStatus,
    Intake,
    Program,
    SearchResult,
    Source,
)
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
        stored: list[Evidence] = []
        for claim in claims:
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
                freshness_deadline=freshness_deadline(claim.claim_type, search_result.retrieved_at),
                content_hash=content_hash((search_result.snippet or "") + claim.claim),
                extraction_model=extraction_model,
                extraction_version=EXTRACTION_VERSION,
            )
            session.add(evidence)
            stored.append(evidence)
        await session.flush()
        return stored

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
        from datetime import UTC, datetime

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
