"""Sync Requirement rows from stored evidence. Deterministic, evidence-first.

Every write is evidence-driven and versioned: a requirement's value/status only
changes when a stored claim says so, and every such change leaves a
`RequirementVersion` snapshot (old -> new with the evidence that caused it).

Claim keys without a requirement home (scholarship, career, policy — see
llm_extract.KNOWN_CLAIM_KEYS) are never synced here: they stay evidence-only.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Evidence,
    EvidenceStatus,
    Requirement,
    RequirementStatus,
    RequirementVersion,
    SourceAuthority,
)
from app.db.repositories import evidence as evidence_repo
from app.services.evidence.llm_extract import KEY_OPERATOR, KEY_TO_REQUIREMENT_TYPE, KNOWN_CLAIM_KEYS

logger = logging.getLogger(__name__)

# Keys whose requirements gate admission, so `mandatory` must be True. Applied
# on create AND re-applied on every sync pass (create-only flags went stale
# when an earlier run had written the row without them).
# Career/policy/scholarship claims are deliberately absent: they are surfaced
# as evidence (and strategy signals), never as gating requirements.
MANDATORY_CLAIM_KEYS = frozenset(
    {"ielts_overall_min", "academic_cgpa_min", "prerequisite_subjects", "application_deadline"}
)

# P2-25: a requirement supported ONLY by a forum thread or an unclassifiable
# domain cannot be asserted as fact — sync flags it NEEDS_VERIFICATION (the
# same flag staleness uses) so the UI asks for a fresh, trustworthy check.
LOW_TRUST_AUTHORITIES = frozenset({SourceAuthority.FORUM_SOCIAL, SourceAuthority.UNKNOWN})


def requirement_title(normalized_key: str) -> str:
    return normalized_key.replace("_", " ").title()


def _is_stale(row: Evidence, now: datetime) -> bool:
    """Past its freshness window (either by deadline or by the stale sweep)."""
    if row.status == EvidenceStatus.STALE:
        return True
    return row.freshness_deadline is not None and row.freshness_deadline <= now


def _change_reason(
    value_changed: bool, status_before: RequirementStatus, status_after: RequirementStatus
) -> str | None:
    parts: list[str] = []
    if value_changed:
        parts.append("value changed")
    if status_before != status_after:
        parts.append(f"status changed: {status_before} -> {status_after}")
    return "; ".join(parts) if parts else None


async def _close_open_version(session: AsyncSession, requirement_id: UUID, now: datetime) -> None:
    """End the currently open version so history has no overlapping windows."""
    open_version = await evidence_repo.open_requirement_version(session, requirement_id)
    if open_version is not None:
        open_version.valid_to = now


async def sync_requirements_from_evidence(
    session: AsyncSession, subject_type: str = "program"
) -> dict[str, int]:
    """Upserts requirements for every program with known-key evidence.

    For each (program, key): the freshest evidence carrying a value supports
    the requirement. When THAT evidence is past its freshness deadline (and is
    not part of a conflict), or comes from a low-trust source (forum/social or
    unclassifiable domain, P2-25), the requirement is flagged
    NEEDS_VERIFICATION instead of pretending the value is current. Any change
    to the value or the status writes a RequirementVersion row capturing
    old/new plus the evidence link; unchanged rows write nothing.
    """
    now = datetime.now(UTC)
    rows = await evidence_repo.known_claim_evidence(session, subject_type, KNOWN_CLAIM_KEYS)

    # Requirements have a FK to programs: skip evidence whose subject no longer
    # (or never) exists rather than crashing the caller with an FK violation.
    subject_ids = {r.subject_id for r in rows if r.subject_id is not None}
    known_program_ids: set[UUID] = set()
    if subject_ids:
        known_program_ids = await evidence_repo.existing_program_ids(session, subject_ids)

    # Source authority of every candidate row (P2-25 low-trust flag), one query.
    source_ids = {r.source_id for r in rows}
    authority_by_source: dict[UUID, SourceAuthority] = {}
    if source_ids:
        authority_by_source = await evidence_repo.source_authorities(session, source_ids)

    # Group every claim per (program, key), freshest first (deterministic: the
    # DB's natural order is not a promise). Conflicting values stay separate
    # Evidence rows — the freshest one merely carries the requirement's value.
    groups: dict[tuple[UUID, str], list[Evidence]] = {}
    for row in rows:
        if row.subject_id is None or not row.normalized_claim:
            continue
        if row.subject_id not in known_program_ids:
            logger.debug("skipping evidence for unknown program subject %s", row.subject_id)
            continue
        groups.setdefault((row.subject_id, row.normalized_claim), []).append(row)
    for group in groups.values():
        group.sort(key=lambda r: (r.retrieved_at, r.id), reverse=True)

    created = 0
    updated = 0
    versions = 0
    for (program_id, key), group in sorted(groups.items(), key=lambda item: (item[0][0], item[0][1])):
        supporting = next((r for r in group if dict(r.extracted_value or {})), None)
        if supporting is None:
            continue  # no value -> nothing to assert (UNKNOWN stays valid)
        value = dict(supporting.extracted_value or {})
        conflicting = any(r.status == EvidenceStatus.CONFLICTING for r in group)
        # Stale or low-trust support that is NOT disputed: the value may have
        # moved on (or never came from a channel we can stand behind), so the
        # requirement needs a fresh look rather than a silent pass/fail.
        low_trust = (
            authority_by_source.get(supporting.source_id, SourceAuthority.UNKNOWN)
            in LOW_TRUST_AUTHORITIES
        )
        verify_status = (
            RequirementStatus.NEEDS_VERIFICATION
            if (not conflicting and (_is_stale(supporting, now) or low_trust))
            else None
        )

        existing = await evidence_repo.requirement_for_key(session, program_id, key)

        if existing is None:
            session.add(
                Requirement(
                    program_id=program_id,
                    requirement_type=KEY_TO_REQUIREMENT_TYPE.get(key, "other"),
                    title=requirement_title(key),
                    normalized_key=key,
                    operator=KEY_OPERATOR.get(key, "eq"),
                    value=value,
                    mandatory=key in MANDATORY_CLAIM_KEYS,
                    status=verify_status or RequirementStatus.UNKNOWN,
                    last_verified_at=supporting.retrieved_at,
                )
            )
            created += 1
            continue

        value_changed = dict(existing.value or {}) != value
        status_before = existing.status
        status_after = verify_status if verify_status is not None else status_before
        mandatory_wanted = key in MANDATORY_CLAIM_KEYS
        if value_changed:
            existing.value = value
        if status_after != status_before:
            existing.status = status_after
        if existing.mandatory != mandatory_wanted:
            existing.mandatory = mandatory_wanted
        existing.last_verified_at = supporting.retrieved_at
        updated += 1

        if value_changed or status_after != status_before:
            reason = _change_reason(value_changed, status_before, status_after)
            await _close_open_version(session, existing.id, now)
            session.add(
                RequirementVersion(
                    requirement_id=existing.id,
                    value=dict(existing.value or {}),
                    status=existing.status,
                    change_reason=reason,
                    evidence_id=supporting.id,
                )
            )
            versions += 1

    await session.commit()
    return {
        "requirements_created": created,
        "requirements_updated": updated,
        "requirement_versions_created": versions,
    }
