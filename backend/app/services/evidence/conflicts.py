"""Conflict detection over normalized claim values. Never silently picks a winner."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
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
)


def _normalize_value(value: dict[str, Any]) -> str:
    """Deterministic canonical form for comparison."""
    parts: list[str] = []
    for key in sorted(value):
        v = value[key]
        if isinstance(v, Decimal):
            v = str(v.normalize())
        parts.append(f"{key}={v}")
    return "|".join(parts)


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
            conflict = EvidenceConflict(
                conflict_key=f"{subject_type}:{subject_id}:{key}",
                description=f"Conflicting values for '{key}' across {len(rows)} sources",
            )
            session.add(conflict)
            await session.flush()
            for row in rows:
                session.add(EvidenceConflictMember(conflict_id=conflict.id, evidence_id=row.id))
                row.status = EvidenceStatus.CONFLICTING
                row.confidence = ConfidenceLevel.LOW
            conflicts.append(conflict)
        await session.commit()
        return conflicts


def conflict_key_for(subject_type: str, subject_id: UUID | str, normalized_key: str) -> str:
    return f"{subject_type}:{subject_id}:{normalized_key}"
