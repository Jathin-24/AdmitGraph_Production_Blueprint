"""Conflict detection over normalized claim values. Never silently picks a winner.

Policy (MASTER_SPEC §11): conflicting sources are kept (never overwritten),
flagged CONFLICTING, confidence downgraded, and surfaced as conflicts the user
must verify. Detection is idempotent: re-running the pipeline re-uses an
existing UNRESOLVED conflict group instead of duplicating rows.

Authority preference (#17): detection records WHICH member the system would
trust if the user asked for a default (`preferred_evidence_id`, highest
Source.source_authority, ties broken by the newest retrieved_at) but the
conflict stays UNRESOLVED — preference is a hint, resolution is the user's.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ConfidenceLevel,
    Evidence,
    EvidenceConflict,
    EvidenceConflictMember,
    EvidenceStatus,
    Source,
    SourceAuthority,
)

# Highest-trust first. The ordering is the declaration order of
# SourceAuthority (official channels above secondary ones above social/unknown).
AUTHORITY_RANK: dict[SourceAuthority, int] = {
    authority: rank for rank, authority in enumerate(SourceAuthority)
}
_UNKNOWN_RANK = len(AUTHORITY_RANK)


def _normalize_value(value: dict[str, Any]) -> str:
    """Deterministic canonical form for comparison."""
    parts: list[str] = []
    for key in sorted(value):
        v = value[key]
        if isinstance(v, Decimal):
            v = str(v.normalize())
        parts.append(f"{key}={v}")
    return "|".join(parts)


def _authority_key(row: Evidence, authority: SourceAuthority | None) -> tuple[int, float]:
    """Sort key: highest authority first, then the newest retrieved_at."""
    rank = AUTHORITY_RANK.get(authority, _UNKNOWN_RANK) if authority is not None else _UNKNOWN_RANK
    retrieved = row.retrieved_at
    if retrieved is None:
        return (rank, 0.0)
    if retrieved.tzinfo is None:  # in-memory rows are built UTC-aware anyway
        retrieved = retrieved.replace(tzinfo=UTC)
    return (rank, -retrieved.timestamp())


async def authority_preferred_evidence(
    session: AsyncSession, rows: list[Evidence]
) -> Evidence | None:
    """The member with the most authoritative source; ties go to the newest.

    Used both to pre-fill `preferred_evidence_id` at detection time and as the
    default when resolving without an explicit choice. Returns None for an
    empty member list.
    """
    if not rows:
        return None
    source_ids = {r.source_id for r in rows if r.source_id is not None}
    sources: dict[UUID, Source] = {}
    if source_ids:
        sources = {
            s.id: s
            for s in (await session.execute(select(Source).where(Source.id.in_(source_ids)))).scalars()
        }
    return min(
        rows,
        key=lambda r: _authority_key(
            r, sources[r.source_id].source_authority if r.source_id in sources else None
        ),
    )


class ConflictDetectionService:
    async def detect_for_subject(
        self, session: AsyncSession, subject_type: str, subject_id: UUID
    ) -> list[EvidenceConflict]:
        result = await session.execute(
            select(Evidence).where(Evidence.subject_type == subject_type, Evidence.subject_id == subject_id)
        )
        evidence_rows = list(result.scalars().all())
        by_key: dict[str, list[Evidence]] = defaultdict(list)
        for row in evidence_rows:
            if row.normalized_claim:
                by_key[row.normalized_claim].append(row)

        conflicts: list[EvidenceConflict] = []
        for key, rows in by_key.items():
            values = {_normalize_value(r.extracted_value) for r in rows}
            if len(values) <= 1:
                continue
            # Temporal relevance: claims retrieved within 180 days of each other conflict.
            rows_sorted = sorted(rows, key=lambda r: r.retrieved_at)
            span = rows_sorted[-1].retrieved_at - rows_sorted[0].retrieved_at
            if span > timedelta(days=180):
                continue
            conflict_key = f"{subject_type}:{subject_id}:{key}"
            # Idempotent: one UNRESOLVED group per conflict key.
            existing = (
                await session.execute(
                    select(EvidenceConflict).where(
                        EvidenceConflict.conflict_key == conflict_key,
                        EvidenceConflict.resolution_status == "UNRESOLVED",
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                conflict = existing
            else:
                conflict = EvidenceConflict(
                    conflict_key=conflict_key,
                    description=f"Conflicting values for '{key}' across {len(rows)} sources",
                )
                session.add(conflict)
                await session.flush()
            for row in rows:
                member = (
                    await session.execute(
                        select(EvidenceConflictMember).where(
                            EvidenceConflictMember.conflict_id == conflict.id,
                            EvidenceConflictMember.evidence_id == row.id,
                        )
                    )
                ).scalar_one_or_none()
                if member is None:
                    session.add(EvidenceConflictMember(conflict_id=conflict.id, evidence_id=row.id))
                # Keep both claims; flag + downgrade confidence (never a winner).
                row.status = EvidenceStatus.CONFLICTING
                row.confidence = ConfidenceLevel.LOW
                row.conflict_group_id = conflict.id
            # Record the authority-preferred member (ties: newest retrieval) as
            # the default the user may accept at resolution time. The conflict
            # itself stays UNRESOLVED: preference is not a silent verdict.
            preferred = await authority_preferred_evidence(session, rows)
            conflict.preferred_evidence_id = preferred.id if preferred is not None else None
            conflicts.append(conflict)
        await session.commit()
        return conflicts


def conflict_key_for(subject_type: str, subject_id: UUID | str, normalized_key: str) -> str:
    return f"{subject_type}:{subject_id}:{normalized_key}"
