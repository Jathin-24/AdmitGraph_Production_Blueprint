"""Bounded evidence recheck (BACKEND_SPEC §Monitoring "On-demand recheck is MVP").

Steps: load previous evidence -> run ONE bounded fresh search -> extract the
current claim for the same subject -> compare normalized values ->
- same value: refresh freshness (STALE -> CURRENT), keep original provenance
- different value: store a NEW evidence row (old evidence is never overwritten)
  and run conflict detection (both rows become CONFLICTING if they differ)
- no provider key: AppError 409 PROVIDER_UNAVAILABLE (no silent fake data)
- too many rechecks by the same caller: AppError 429 RATE_LIMITED (P1-10
  cooldown, only when the API passes rate_key)

Every provider request is recorded in `search_runs` (serpapi_docs rule 5).
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.db.models import (
    Evidence,
    EvidenceStatus,
    Program,
    RunStatus,
    SearchResult,
    SearchRun,
    Source,
)
from app.db.repositories import evidence as evidence_repo
from app.db.repositories.programs import get_program_by_id
from app.services.evidence.conflicts import ConflictDetectionService, _normalize_value
from app.services.evidence.extraction import EvidenceExtractionService, ExtractedClaim
from app.services.evidence.freshness import freshness_deadline
from app.services.evidence.llm_extract import extract_claims
from app.services.llm import get_llm_provider
from app.services.serpapi.authority import classify_domain
from app.services.serpapi.cache import SearchCache
from app.services.serpapi.client import SerpApiClient, SerpApiError

logger = logging.getLogger(__name__)

MAX_RECHECK_RESULTS = 3

# P1-10: per-caller cooldown for the on-demand recheck (a recheck costs a real
# provider search). Sliding window: at most RECHECK_MAX_PER_WINDOW calls per
# RECHECK_WINDOW_SECONDS and per rate_key. Direct service callers (workers,
# tests) pass rate_key=None and bypass the cooldown entirely.
RECHECK_WINDOW_SECONDS = 60
RECHECK_MAX_PER_WINDOW = 5
_RECHECK_HITS: dict[str, list[float]] = {}


def reset_recheck_cooldown(rate_key: str | None = None) -> None:
    """Drop the sliding window for one caller (or all, for tests/fixtures)."""
    if rate_key is None:
        _RECHECK_HITS.clear()
    else:
        _RECHECK_HITS.pop(rate_key, None)


def _recheck_retry_after(rate_key: str) -> float:
    """Seconds until this caller may recheck again (0 = allowed now).

    Prunes stale hits as a side effect so the window stays bounded.
    """
    now = time.monotonic()
    hits = [t for t in _RECHECK_HITS.get(rate_key, []) if now - t < RECHECK_WINDOW_SECONDS]
    _RECHECK_HITS[rate_key] = hits
    if len(hits) < RECHECK_MAX_PER_WINDOW:
        return 0.0
    return max(0.0, RECHECK_WINDOW_SECONDS - (now - hits[0]))


def _record_recheck_hit(rate_key: str) -> None:
    _RECHECK_HITS.setdefault(rate_key, []).append(time.monotonic())


def _recheck_query(evidence: Evidence, program: Program | None) -> str:
    """Deterministic query built only from stored data (no invented facts)."""
    key = (evidence.normalized_claim or "").replace("_", " ").strip()
    if program is not None:
        return f"{program.canonical_name} {key}".strip()
    snippet = (evidence.claim or "").split(".")[0][:80]
    return f"{key} {snippet}".strip() or key


async def _upsert_source(session: AsyncSession, url: str, title: str | None) -> Source | None:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    domain = (parsed.netloc or "").lower()
    canonical = url.split("#")[0]
    existing = await evidence_repo.source_by_canonical_url(session, canonical)
    if existing is not None:
        existing.last_seen_at = datetime.now(UTC)
        return existing
    source = Source(
        url=url,
        canonical_url=canonical,
        domain=domain,
        title=title,
        source_authority=classify_domain(domain),
        last_seen_at=datetime.now(UTC),
    )
    session.add(source)
    await session.flush()
    return source


async def recheck_evidence(
    session: AsyncSession,
    evidence_id: UUID,
    *,
    settings: Settings | None = None,
    serpapi: Any | None = None,
    rate_key: str | None = None,
) -> dict[str, Any]:
    """Re-check one claim with one bounded fresh search. Returns the updated item.

    Error order (P1-10): 404 unknown evidence -> 429 per-caller cooldown ->
    409 provider unavailable. ``rate_key`` is the API's caller identity;
    ``None`` skips the cooldown (workers / direct service calls).
    """
    settings = settings or get_settings()
    evidence = await evidence_repo.get_evidence(session, evidence_id)
    if evidence is None:
        raise AppError(404, "NOT_FOUND", "Evidence not found")
    if rate_key is not None:
        retry_after = _recheck_retry_after(rate_key)
        if retry_after > 0:
            raise AppError(
                429,
                "RATE_LIMITED",
                f"Too many rechecks — try again in {int(retry_after) + 1} seconds",
            )
        _record_recheck_hit(rate_key)
    if not settings.serpapi_api_key and serpapi is None:
        raise AppError(
            409, "PROVIDER_UNAVAILABLE", "Provider unavailable — configure SERPAPI_API_KEY"
        )

    program: Program | None = None
    if evidence.subject_type == "program" and evidence.subject_id is not None:
        program = await get_program_by_id(session, evidence.subject_id)

    client = serpapi or SerpApiClient(settings, cache=SearchCache(settings))
    query = _recheck_query(evidence, program)

    recheck_info: dict[str, Any] = {"query": query, "refreshed": False, "new_evidence_id": None}
    try:
        result = await client.search("google", query)
    except SerpApiError as exc:
        raise AppError(502, exc.code, f"Evidence recheck failed: {exc}") from exc

    run = SearchRun(
        engine="google",
        query=query,
        parameters={"purpose": "recheck", "evidence_id": str(evidence.id)},
        serpapi_search_id=result.search_id,
        status=RunStatus.SUCCEEDED,
        duration_ms=result.duration_ms,
        result_count=result.result_count,
        cache_hit=result.cache_hit,
    )
    session.add(run)
    await session.commit()

    # Extract the current claim from the freshest results for this subject.
    provider = get_llm_provider(settings)
    matched_claim: dict[str, Any] | None = None
    matched_source: Source | None = None
    matched_search_result: SearchResult | None = None
    for item in result.organic_results[:MAX_RECHECK_RESULTS]:
        url = item.get("link") or item.get("url")
        if not url:
            continue
        source = await _upsert_source(session, url, item.get("title"))
        if source is None:
            continue
        search_result = SearchResult(
            search_run_id=run.id,
            source_id=source.id,
            position=1,
            result_type="google",
            title=item.get("title"),
            snippet=item.get("snippet"),
            displayed_url=item.get("displayed_link"),
            result_url=url,
            raw_payload=item,
            retrieved_at=datetime.now(UTC),
        )
        session.add(search_result)
        await session.flush()
        claims = await extract_claims(
            provider, title=item.get("title"), snippet=item.get("snippet"), domain=source.domain
        )
        if claims is None:
            continue
        for claim in claims.claims:
            if claim.normalized_key == evidence.normalized_claim:
                matched_claim = {
                    "claim_type": claim.claim_type,
                    "normalized_key": claim.normalized_key,
                    "value": claim.value,
                    "claim": claim.claim,
                    "confidence": claim.confidence,
                }
                matched_source = source
                matched_search_result = search_result
                break
        if matched_claim is not None:
            break

    if matched_claim is None or matched_source is None or matched_search_result is None:
        # No fresh confirmation: the existing row keeps its honest state.
        recheck_info["found"] = False
    else:
        from app.db.models import ConfidenceLevel

        same_value = _normalize_value(dict(matched_claim["value"])) == _normalize_value(
            dict(evidence.extracted_value or {})
        )
        if same_value:
            # Same claim re-verified: refresh freshness, keep original provenance.
            # CONFLICTING rows keep their flag: a recheck must not silently
            # resolve a conflict the user still has to verify.
            if evidence.status != EvidenceStatus.CONFLICTING:
                evidence.status = EvidenceStatus.CURRENT
            evidence.freshness_deadline = freshness_deadline(evidence.claim_type, datetime.now(UTC))
            recheck_info["refreshed"] = True
        else:
            # Differing value: store a NEW row (never overwrite old evidence).
            service = EvidenceExtractionService()
            stored = await service.record_claims(
                session,
                matched_source,
                matched_search_result,
                [
                    ExtractedClaim(
                        claim_type=str(matched_claim["claim_type"]),
                        normalized_key=str(matched_claim["normalized_key"]),
                        value=dict(matched_claim["value"]),
                        claim=str(matched_claim["claim"]),
                        confidence=ConfidenceLevel(str(matched_claim["confidence"]).upper())
                        if str(matched_claim["confidence"]).upper() in ("HIGH", "MEDIUM", "LOW")
                        else ConfidenceLevel.LOW,
                        subject_type=evidence.subject_type,
                        subject_id=evidence.subject_id,
                    )
                ],
            )
            if stored:
                recheck_info["new_evidence_id"] = str(stored[0].id)
            # Conflict detection: differing values -> CONFLICTING on both.
            if evidence.subject_id is not None:
                await ConflictDetectionService().detect_for_subject(
                    session, evidence.subject_type, evidence.subject_id
                )
            recheck_info["refreshed"] = True
        recheck_info["found"] = True

    await session.commit()
    await session.refresh(evidence)

    return {
        "id": str(evidence.id),
        "claim_type": evidence.claim_type,
        "subject_type": evidence.subject_type,
        "subject_id": str(evidence.subject_id) if evidence.subject_id else None,
        "claim": evidence.claim,
        "normalized_claim": evidence.normalized_claim,
        "extracted_value": evidence.extracted_value,
        "confidence": evidence.confidence.value,
        "status": evidence.status.value,
        "retrieved_at": evidence.retrieved_at.isoformat() if evidence.retrieved_at else None,
        "freshness_deadline": (
            evidence.freshness_deadline.isoformat() if evidence.freshness_deadline else None
        ),
        "extraction_version": evidence.extraction_version,
        "source_id": str(evidence.source_id),
        "search_result_id": str(evidence.search_result_id) if evidence.search_result_id else None,
        "recheck": recheck_info,
    }
