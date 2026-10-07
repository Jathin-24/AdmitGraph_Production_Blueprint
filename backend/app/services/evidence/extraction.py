from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ConfidenceLevel, Evidence, EvidenceStatus, SearchResult, Source
from app.services.evidence.freshness import freshness_deadline

EXTRACTION_VERSION = "v1"


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
