"""On-demand monitoring: observe the current value, compare, snapshot, alert.

Implements BACKEND_SPEC §Monitoring steps 1–8 for one subscription:

1. load previous evidence (latest snapshot for the field),
2. run a bounded fresh search — exactly one SerpApi query when a key is
   configured, never more,
3. extract the current claim through the research pipeline's LLM extraction
   (read-only import from ``services.evidence``),
4. compare normalized values with the materiality rules,
5. create a snapshot (normalized scalars, ``change_type = field_key``),
6. flag material change,
7. attach the evidence used,
8. update freshness and advance ``last_checked_at`` / ``next_check_at``.

Honesty rules: when no SerpApi key is configured (tests, local default) — or
the live search/extraction yields no usable claim — the check falls back to
the latest *stored* evidence for the field and the explanation says so
("based on last stored evidence"). Values are only ever observed or carried
forward; nothing is invented.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.db.models import (
    ConfidenceLevel,
    Evidence,
    EvidenceStatus,
    Institution,
    MonitorSnapshot,
    MonitorSubscription,
    Program,
    Source,
    SourceAuthority,
    StudentProfile,
)
from app.services.evidence.extraction import content_hash
from app.services.evidence.freshness import freshness_deadline
from app.services.evidence.llm_extract import LLMClaim, extract_claims
from app.services.llm import get_llm_provider
from app.services.monitoring.materiality import (
    FIELD_LABELS,
    ScalarValue,
    explain_change,
    material_change_for,
    next_check_time,
    to_scalar,
)
from app.services.monitoring.materiality import (
    is_material_change as is_material_change,
)
from app.services.notifications import create_notification, has_recent_notification
from app.services.serpapi.client import SerpApiClient

log = logging.getLogger(__name__)

# Notification types for the 5 FRONTEND_SPEC monitoring cards (per watched field).
_NOTIFICATION_TYPES: dict[str, str] = {
    "deadline": "DEADLINE_CHANGED",
    "cost": "COST_CHANGED",
    "academic": "REQUIREMENT_CHANGED",
    "prerequisite": "REQUIREMENT_CHANGED",
    "scholarship": "SCHOLARSHIP_SIGNAL",
}
_NOTIFICATION_TITLES: dict[str, str] = {
    "DEADLINE_CHANGED": "Deadline changed",
    "COST_CHANGED": "Cost changed",
    "REQUIREMENT_CHANGED": "Requirement changed",
    "SCHOLARSHIP_SIGNAL": "New scholarship signal",
}
# claim_type values stored by the research pipeline for each watched field.
_CACHED_CLAIM_TYPES: dict[str, tuple[str, ...]] = {
    "deadline": ("deadline",),
    "cost": ("cost", "tuition", "fee"),
    "academic": ("academic",),
    "prerequisite": ("prerequisite",),
    "scholarship": ("scholarship", "fee", "tuition"),
}
# normalized_key / claim_type values the LLM extraction uses for each field.
_CLAIM_KEYS: dict[str, tuple[str, ...]] = {
    "deadline": ("application_deadline", "deadline"),
    "cost": ("tuition_max", "fee_max", "fee", "tuition"),
    "academic": ("cgpa_min", "academic_cgpa_min", "backlogs_max"),
    "prerequisite": ("prerequisite_subjects",),
    "scholarship": ("scholarship", "scholarship_deadline", "funding"),
}
_CLAIM_TYPES: dict[str, tuple[str, ...]] = {
    "deadline": ("deadline",),
    "cost": ("tuition", "fee"),
    "academic": ("academic",),
    "prerequisite": ("prerequisite",),
    "scholarship": ("scholarship", "financial_aid"),
}
# A live observation shares its timestamp with the snapshot it produced, so a
# referenced evidence row retrieved within this window means "observed now".
_LIVE_TIMESTAMP_TOLERANCE_SECONDS = 1.0


@dataclass(frozen=True)
class _Observation:
    """Result of one check's observation step."""

    value: ScalarValue | None
    source: str  # "live" (fresh search + extraction) | "cached" (stored evidence)
    evidence_ids: tuple[str, ...]


def _jsonb(value: ScalarValue | None) -> dict[str, Any] | None:
    # MonitorSnapshot.old_value/new_value are annotated dict (the JSONB column
    # predates scalar snapshot values); the column itself stores any JSON value.
    return cast("dict[str, Any] | None", value)


def _evidence_id_list(snapshot: MonitorSnapshot) -> list[UUID]:
    ids: list[UUID] = []
    for raw in snapshot.evidence_ids or []:
        try:
            ids.append(UUID(str(raw)))
        except (ValueError, TypeError, AttributeError):
            continue
    return ids


def _domain_of(url: str) -> str | None:
    from urllib.parse import urlparse

    text = url.strip()
    if not text:
        return None
    parsed = urlparse(text if "://" in text else f"//{text}", scheme="")
    host = (parsed.netloc or parsed.path.split("/", 1)[0]).strip().lower()
    if host.startswith("www."):
        host = host[4:]
    return host if "." in host else None


async def _retrieved_map(session: AsyncSession, ids: list[UUID]) -> dict[UUID, datetime]:
    if not ids:
        return {}
    rows = await session.execute(select(Evidence.id, Evidence.retrieved_at).where(Evidence.id.in_(ids)))
    return {row_id: retrieved for row_id, retrieved in rows.all()}


def _is_live(retrieved: dict[UUID, datetime], snapshot: MonitorSnapshot) -> bool:
    """True when the snapshot referenced evidence observed during that check."""
    for evidence_id in _evidence_id_list(snapshot):
        timestamp = retrieved.get(evidence_id)
        if timestamp is None:
            continue
        delta = abs((timestamp - snapshot.checked_at).total_seconds())
        if delta <= _LIVE_TIMESTAMP_TOLERANCE_SECONDS:
            return True
    return False


async def explanation_for_snapshot(
    session: AsyncSession, snapshot: MonitorSnapshot, *, include_immaterial: bool = False
) -> str | None:
    """Recompute the explanation for a stored snapshot.

    The snapshot table has no explanation column (no schema changes allowed),
    so the sentence is regenerated from the same stored scalars and the
    source is derived from the evidence timestamps the snapshot links to.
    Immaterial snapshots return ``None`` unless ``include_immaterial`` (the
    check endpoint always explains, history only surfaces material changes).
    """
    if not snapshot.material_change and not include_immaterial:
        return None
    retrieved = await _retrieved_map(session, _evidence_id_list(snapshot))
    source = "live" if _is_live(retrieved, snapshot) else "cached"
    return explain_change(
        snapshot.field_key,
        snapshot.old_value,
        snapshot.new_value,
        material=snapshot.material_change,
        source=source,
    )


async def explanations_for_snapshots(
    session: AsyncSession, snapshots: list[MonitorSnapshot]
) -> dict[UUID, str | None]:
    """Batched :func:`explanation_for_snapshot` for the changes history."""
    ids: list[UUID] = []
    for snapshot in snapshots:
        if snapshot.material_change:
            ids.extend(_evidence_id_list(snapshot))
    retrieved = await _retrieved_map(session, ids)
    out: dict[UUID, str | None] = {}
    for snapshot in snapshots:
        if not snapshot.material_change:
            out[snapshot.id] = None
            continue
        source = "live" if _is_live(retrieved, snapshot) else "cached"
        out[snapshot.id] = explain_change(
            snapshot.field_key,
            snapshot.old_value,
            snapshot.new_value,
            material=True,
            source=source,
        )
    return out


async def _program_name(session: AsyncSession, subscription: MonitorSubscription) -> str:
    if subscription.program_id is not None:
        program = await session.get(Program, subscription.program_id)
        if program is not None:
            return program.canonical_name
    return "Your monitored program"


async def _evidence_domain(
    session: AsyncSession, subscription: MonitorSubscription, field_key: str
) -> str | None:
    """Official domain hint from the evidence already stored for this subject."""
    stmt = (
        select(Source.domain)
        .join(Evidence, Evidence.source_id == Source.id)
        .where(Evidence.subject_type == "program")
        .order_by(Evidence.retrieved_at.desc())
        .limit(1)
    )
    if subscription.program_id is not None:
        stmt = stmt.where(Evidence.subject_id == subscription.program_id)
    else:
        stmt = stmt.where(Evidence.claim_type.in_(_CACHED_CLAIM_TYPES.get(field_key, (field_key,))))
    domain = (await session.execute(stmt)).scalar_one_or_none()
    return _domain_of(domain) if domain else None


async def _build_query(
    session: AsyncSession, subscription: MonitorSubscription, field_key: str
) -> tuple[str, str | None]:
    """Bounded query: program's official domain (or evidence source) + field."""
    name = "the program"
    domain: str | None = None
    if subscription.program_id is not None:
        program = await session.get(Program, subscription.program_id)
        if program is not None:
            name = program.canonical_name
            domain = _domain_of(program.official_url) if program.official_url else None
            if domain is None:
                institution_domain = (
                    await session.execute(
                        select(Institution.domain).where(Institution.id == program.institution_id)
                    )
                ).scalar_one_or_none()
                domain = _domain_of(institution_domain) if institution_domain else None
    if domain is None:
        domain = await _evidence_domain(session, subscription, field_key)
    label = FIELD_LABELS.get(field_key, field_key)
    if domain:
        return f"site:{domain} {name} {label}", domain
    return f"{name} {label} official", None


def _pick_claim(claims: list[LLMClaim], field_key: str) -> LLMClaim | None:
    """Only claims that actually speak to the watched field are used."""
    keys = _CLAIM_KEYS.get(field_key, ())
    claim_types = _CLAIM_TYPES.get(field_key, ())
    for claim in claims:
        if claim.normalized_key in keys:
            return claim
    for claim in claims:
        if claim.claim_type in claim_types:
            return claim
    for claim in claims:
        haystack = f"{claim.normalized_key} {claim.claim_type}".lower()
        if field_key in haystack:
            return claim
    return None


async def _persist_live_evidence(
    session: AsyncSession,
    subscription: MonitorSubscription,
    field_key: str,
    claim: LLMClaim,
    top_result: dict[str, Any],
    fallback_domain: str | None,
    settings: Settings,
    observed_at: datetime,
) -> Evidence | None:
    """Attach the fresh observation as evidence (step 7) so provenance survives."""
    link = str(top_result.get("link") or "").strip()
    if not link:
        return None  # no citable URL -> no evidence row (never fabricate a source)
    canonical = link.split("#", 1)[0]
    source = (
        await session.execute(select(Source).where(Source.canonical_url == canonical))
    ).scalar_one_or_none()
    if source is None:
        source = Source(
            url=link,
            canonical_url=canonical,
            domain=_domain_of(link) or fallback_domain or "",
            title=str(top_result.get("title") or "") or None,
            source_authority=SourceAuthority.UNKNOWN,
        )
        session.add(source)
        await session.flush()
    try:
        confidence = ConfidenceLevel(str(claim.confidence).upper())
    except ValueError:
        confidence = ConfidenceLevel.LOW
    endpoints = settings.llm_endpoints
    evidence = Evidence(
        source_id=source.id,
        search_result_id=None,
        claim_type=field_key,
        subject_type="program",
        subject_id=subscription.program_id,
        claim=claim.claim,
        normalized_claim=claim.normalized_key,
        snippet=str(top_result.get("snippet") or "") or None,
        extracted_value=claim.value,
        confidence=confidence,
        status=EvidenceStatus.CURRENT,
        retrieved_at=observed_at,
        freshness_deadline=freshness_deadline(field_key, observed_at),
        content_hash=content_hash(claim.claim),
        extraction_model=endpoints[0]["model"] if endpoints else None,
        extraction_version="monitor-v1",
    )
    session.add(evidence)
    await session.flush()
    return evidence


async def _observe_live(
    session: AsyncSession,
    subscription: MonitorSubscription,
    field_key: str,
    settings: Settings,
    observed_at: datetime,
) -> _Observation:
    query, domain = await _build_query(session, subscription, field_key)
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as http:
        client = SerpApiClient(settings, http_client=http)
        result = await client.google(query)  # exactly one SerpApi search per check
    hits = result.organic_results
    if not hits:
        return _Observation(None, "live", ())
    top = hits[0]
    top_domain = _domain_of(str(top.get("link") or "")) or domain
    provider = get_llm_provider(settings)
    claims = await extract_claims(
        provider,
        title=str(top.get("title") or "") or None,
        snippet=str(top.get("snippet") or "") or None,
        domain=top_domain,
    )
    if claims is None or not claims.claims:
        return _Observation(None, "live", ())
    claim = _pick_claim(claims.claims, field_key)
    if claim is None:
        return _Observation(None, "live", ())
    value = to_scalar(claim.value, field_key)
    if value is None or value == "":
        return _Observation(None, "live", ())
    evidence = await _persist_live_evidence(
        session, subscription, field_key, claim, top, top_domain, settings, observed_at
    )
    evidence_ids = (str(evidence.id),) if evidence is not None else ()
    return _Observation(value, "live", evidence_ids)


async def _observe_cached(
    session: AsyncSession, subscription: MonitorSubscription, field_key: str
) -> _Observation:
    """Honest fallback: compare against the latest stored evidence for the field."""
    claim_types = _CACHED_CLAIM_TYPES.get(field_key, (field_key,))
    conditions = [Evidence.claim_type.in_(claim_types)]
    if field_key == "scholarship":
        conditions.append(Evidence.normalized_claim.ilike("%scholarship%"))
    stmt = select(Evidence).where(Evidence.subject_type == "program", or_(*conditions))
    if subscription.program_id is not None:
        stmt = stmt.where(Evidence.subject_id == subscription.program_id)
    latest = (
        await session.execute(stmt.order_by(Evidence.retrieved_at.desc()).limit(1))
    ).scalar_one_or_none()
    if latest is None:
        return _Observation(None, "cached", ())
    return _Observation(to_scalar(latest.extracted_value, field_key), "cached", (str(latest.id),))


async def _observe(
    session: AsyncSession,
    subscription: MonitorSubscription,
    field_key: str,
    observed_at: datetime,
) -> _Observation:
    settings = get_settings()
    if settings.serpapi_api_key.strip():
        try:
            live = await _observe_live(session, subscription, field_key, settings, observed_at)
            if live.value is not None:
                return live
        except Exception as exc:  # noqa: BLE001 - a provider failure degrades, never breaks
            log.warning(
                "live monitor check unavailable for subscription %s (%s); "
                "comparing against stored evidence",
                subscription.id,
                type(exc).__name__,
            )
    return await _observe_cached(session, subscription, field_key)


class MonitoringService:
    """On-demand re-check for a single monitor subscription."""

    async def run_check(
        self,
        session: AsyncSession,
        subscription_id: UUID,
        *,
        owner_profile_id: UUID | None = None,
    ) -> MonitorSnapshot:
        """On-demand check for one subscription.

        `owner_profile_id` scopes the load to a caller's profile: HTTP callers
        pass the requesting profile (cross-user ids 404 like missing ones),
        while the background scheduler passes none (system scope).
        """
        sub = await session.get(MonitorSubscription, subscription_id)
        if sub is None or (owner_profile_id is not None and sub.profile_id != owner_profile_id):
            raise AppError(404, "NOT_FOUND", "Monitor subscription not found")
        field_key = sub.field_key
        now = datetime.now(UTC)

        # 1. previous evidence (baseline from the last snapshot, if any)
        previous = (
            await session.execute(
                select(MonitorSnapshot)
                .where(
                    MonitorSnapshot.subscription_id == subscription_id,
                    MonitorSnapshot.field_key == field_key,
                )
                .order_by(MonitorSnapshot.checked_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        old_value = to_scalar(previous.new_value, field_key) if previous is not None else None

        # 2+3. bounded fresh search + extraction, or honest cached comparison
        observation = await _observe(session, sub, field_key, now)
        new_value = observation.value

        # 4. compare normalized values per the materiality rules
        material = material_change_for(field_key, old_value, new_value)
        explanation = explain_change(
            field_key, old_value, new_value, material=material, source=observation.source
        )

        # 5+6+7. snapshot with material flag and linked evidence
        snapshot = MonitorSnapshot(
            subscription_id=subscription_id,
            field_key=field_key,
            old_value=_jsonb(old_value),
            new_value=_jsonb(new_value),
            change_type=field_key,
            evidence_ids=list(observation.evidence_ids),
            material_change=material,
            checked_at=now,
        )
        session.add(snapshot)
        await session.flush()

        program_name = await _program_name(session, sub)
        if material:
            await _notify_change(session, sub, snapshot, program_name, explanation,
                                 observation.source)
        await _notify_conflicts(session, sub, program_name)

        # 8. update freshness + advance the schedule (manual and scheduled alike)
        sub.last_checked_at = now
        sub.next_check_at = next_check_time(now, sub.frequency)
        await session.commit()
        return snapshot


async def _notify_change(
    session: AsyncSession,
    subscription: MonitorSubscription,
    snapshot: MonitorSnapshot,
    program_name: str,
    explanation: str,
    source: str,
) -> None:
    profile = await session.get(StudentProfile, subscription.profile_id)
    if profile is None:
        return
    notification_type = _NOTIFICATION_TYPES.get(subscription.field_key, "REQUIREMENT_CHANGED")
    title = _NOTIFICATION_TITLES.get(notification_type, "Requirement changed")
    payload: dict[str, Any] = {
        "subscription_id": str(subscription.id),
        "snapshot_id": str(snapshot.id),
        "program_id": str(subscription.program_id) if subscription.program_id else None,
        "field_key": subscription.field_key,
        "old_value": snapshot.old_value,
        "new_value": snapshot.new_value,
        "explanation": explanation,
        "source": source,
    }
    await create_notification(
        session,
        profile.user_id,
        notification_type,
        title,
        f"{program_name}: {explanation}",
        link="/monitor",
        payload=payload,
        send_email="change_alert",
    )


async def _notify_conflicts(
    session: AsyncSession, subscription: MonitorSubscription, program_name: str
) -> None:
    """Emit CONFLICT_DETECTED only when conflicts are actually detectable.

    Detection is read-only: the research pipeline's conflict service marks
    evidence rows CONFLICTING, and this check reports that stored state
    (deduped per user + program within 7 days).
    """
    if subscription.program_id is None:
        return
    conflicting = (
        await session.execute(
            select(Evidence.id)
            .where(
                Evidence.subject_type == "program",
                Evidence.subject_id == subscription.program_id,
                Evidence.status == EvidenceStatus.CONFLICTING,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if conflicting is None:
        return
    profile = await session.get(StudentProfile, subscription.profile_id)
    if profile is None:
        return
    program_id = str(subscription.program_id)
    if await has_recent_notification(
        session, profile.user_id, "CONFLICT_DETECTED", "program_id", program_id, days=7
    ):
        return
    label = FIELD_LABELS.get(subscription.field_key, subscription.field_key)
    await create_notification(
        session,
        profile.user_id,
        "CONFLICT_DETECTED",
        "Conflicting information detected",
        f"{program_name}: stored sources disagree about the {label} for this program.",
        link="/monitor",
        payload={"program_id": program_id, "field_key": subscription.field_key},
        send_email=None,
    )
