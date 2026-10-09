"""P1-10 — ownership scoping for evidence and conflict endpoints.

Access rule (PLAN.md P1-10 "evidence/conflict endpoints ownership-scoped"):

1. **Attached** — the evidence (or any member of its conflict group) is
   attached to the caller's profile. Attachments are the profile-scoped rows
   that reference the evidence or its subject program:

   * direct evidence links: ``risk_evidence`` (via the caller's ``Risk``),
     ``fit_dimension_evidence`` / ``eligibility_evidence`` (via the caller's
     ``FitAssessment``) and ``roadmap_tasks.evidence_ids`` (JSONB ``@>``);
   * subject-program links: the caller's ``SavedProgram``,
     ``ApplicationPlan``, ``FitAssessment``, ``MonitorSubscription`` or
     ``Risk`` for that program.

2. **Shared corpus** — the caller is in demo mode (anonymous traffic, or the
   ``demo@admitgraph.local`` account itself) AND the evidence is not attached
   to any *non-demo* profile. Unclaimed evidence belongs to everyone;
   evidence another real student has attached is theirs alone.

Anything else is a 404 — the API's 404-not-403 convention, so a foreign id is
indistinguishable from a non-existent one.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.models import (
    Evidence,
    EvidenceConflict,
    StudentProfile,
)
from app.db.repositories import evidence as evidence_repo
from app.db.repositories.auth import get_user
from app.services.profile import DEMO_EMAIL, get_or_create_profile


async def attached_profile_ids(session: AsyncSession, evidence: Evidence) -> set[uuid.UUID]:
    """Every profile the evidence is attached to (rule 1, both branches)."""
    attached: set[uuid.UUID] = set()

    # --- direct evidence links ------------------------------------------------
    attached |= await evidence_repo.risk_evidence_profile_ids(session, evidence.id)
    attached |= await evidence_repo.fit_dimension_evidence_profile_ids(session, evidence.id)
    attached |= await evidence_repo.eligibility_evidence_profile_ids(session, evidence.id)
    attached |= await evidence_repo.roadmap_task_profile_ids(session, evidence.id)

    # --- subject-program links ------------------------------------------------
    if evidence.subject_type == "program" and evidence.subject_id is not None:
        program_id = evidence.subject_id
        attached |= await evidence_repo.saved_program_profile_ids(session, program_id)
        attached |= await evidence_repo.application_plan_profile_ids(session, program_id)
        attached |= await evidence_repo.fit_program_profile_ids(session, program_id)
        attached |= await evidence_repo.monitor_subscription_profile_ids(session, program_id)
        attached |= await evidence_repo.risk_program_profile_ids(session, program_id)
    return attached


async def _claimed_by_real_profile(
    session: AsyncSession, profile_ids: set[uuid.UUID]
) -> bool:
    """True when any of ``profile_ids`` belongs to a non-demo account."""
    if not profile_ids:
        return False
    emails = await evidence_repo.profile_emails(session, profile_ids)
    return any(email != DEMO_EMAIL for email in emails)


async def caller_context(session: AsyncSession) -> tuple[StudentProfile, bool]:
    """(caller's profile, is_demo_mode) — demo mode per rule 2."""
    from app.core.security import current_user_id

    profile = await get_or_create_profile(session)
    if current_user_id() is None:
        return profile, True
    user = await get_user(session, profile.user_id)
    return profile, user is not None and user.email == DEMO_EMAIL


async def can_access_evidence(
    session: AsyncSession, evidence: Evidence, profile: StudentProfile, is_demo: bool
) -> bool:
    attached = await attached_profile_ids(session, evidence)
    if profile.id in attached:
        return True
    if not is_demo:
        return False
    # Demo mode reads the shared corpus — evidence no real student owns.
    return not await _claimed_by_real_profile(session, attached)


async def can_access_conflict(
    session: AsyncSession, conflict: EvidenceConflict, profile: StudentProfile, is_demo: bool
) -> bool:
    """Same rule as evidence, unioned across the conflict's members."""
    member_ids = await evidence_repo.conflict_member_evidence_ids(session, conflict.id)
    attached: set[uuid.UUID] = set()
    for evidence_id in member_ids:
        member = await evidence_repo.get_evidence(session, evidence_id)
        if member is not None:
            attached |= await attached_profile_ids(session, member)
    if profile.id in attached:
        return True
    if not is_demo:
        return False
    return not await _claimed_by_real_profile(session, attached)


async def require_evidence_access(
    session: AsyncSession, evidence: Evidence
) -> tuple[StudentProfile, bool]:
    """Raise 404 unless the caller may read this evidence; else the context."""
    profile, is_demo = await caller_context(session)
    if not await can_access_evidence(session, evidence, profile, is_demo):
        raise AppError(404, "NOT_FOUND", "Evidence not found")
    return profile, is_demo


async def require_conflict_access(
    session: AsyncSession, conflict: EvidenceConflict
) -> tuple[StudentProfile, bool]:
    """Raise 404 unless the caller may act on this conflict; else the context."""
    profile, is_demo = await caller_context(session)
    if not await can_access_conflict(session, conflict, profile, is_demo):
        raise AppError(404, "NOT_FOUND", "Conflict not found")
    return profile, is_demo
