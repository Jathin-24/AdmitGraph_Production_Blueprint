"""Replay fixture for the demo run: load and idempotently apply captured data.

backend/scripts/demo/example.json is captured from a REAL completed research
run (scripts/capture_example.py): institutions, programs, sources, claims and
requirements are real rows with real provenance — the demo never invents
university facts. Applying the fixture twice must not duplicate anything:
every upsert is keyed on the model's natural unique key (or, for evidence,
which has no unique constraint, the captured row's own primary key).
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.models import (
    ConfidenceLevel,
    Evidence,
    EvidenceStatus,
    Institution,
    Intake,
    Program,
    Requirement,
    RequirementStatus,
    Source,
    SourceAuthority,
)

FIXTURE_PATH = Path(__file__).resolve().parents[3] / "scripts" / "demo" / "example.json"


class FixtureMeta(BaseModel):
    captured_at: str
    source_run_id: str
    note: str
    requested_goal: dict[str, Any] = {}


class FixtureInstitution(BaseModel):
    id: str
    canonical_name: str
    normalized_name: str
    country_code: str | None = None
    city: str | None = None
    website_url: str | None = None
    domain: str | None = None
    institution_type: str | None = None
    authority_score: Decimal | None = None


class FixtureProgram(BaseModel):
    id: str
    institution_ref: str
    canonical_name: str
    normalized_name: str
    degree_type: str | None = None
    field_of_study: str | None = None
    specialization: str | None = None
    city: str | None = None
    country_code: str | None = None
    language: str | None = None
    official_url: str | None = None
    duration_months: int | None = None
    tuition_amount: Decimal | None = None
    tuition_currency: str | None = None
    extra: dict[str, Any] = {}
    last_verified_at: str | None = None
    active: bool = True


class FixtureSource(BaseModel):
    id: str
    url: str
    canonical_url: str
    domain: str
    title: str | None = None
    source_authority: SourceAuthority = SourceAuthority.UNKNOWN
    publisher: str | None = None
    country_code: str | None = None
    source_type: str | None = None


class FixtureEvidence(BaseModel):
    id: str
    source_ref: str
    claim_type: str
    subject_type: str = "program"
    subject_ref: str | None = None
    claim: str
    normalized_claim: str | None = None
    snippet: str | None = None
    summary: str | None = None
    extracted_value: dict[str, Any] = {}
    authority_score: Decimal | None = None
    confidence: ConfidenceLevel = ConfidenceLevel.LOW
    status: EvidenceStatus = EvidenceStatus.CURRENT
    retrieved_at: str
    published_at: str | None = None
    freshness_deadline: str | None = None
    content_hash: str | None = None
    extraction_model: str | None = None
    extraction_version: str | None = None
    # Provenance captured alongside the claim for display/validation:
    source_url: str | None = None
    source_domain: str | None = None
    source_authority: str | None = None


class FixtureRequirement(BaseModel):
    id: str
    program_ref: str
    requirement_type: str
    title: str
    normalized_key: str
    operator: str | None = None
    value: dict[str, Any]
    mandatory: bool = True
    applies_to: dict[str, Any] = {}
    status: RequirementStatus = RequirementStatus.UNKNOWN
    last_verified_at: str | None = None


class FixtureIntake(BaseModel):
    id: str
    program_ref: str
    intake_label: str
    intake_year: int
    start_date: str | None = None
    application_deadline: str | None = None
    deadline_type: str | None = None
    status: str | None = None


class DemoFixture(BaseModel):
    meta: FixtureMeta
    planned_queries: list[dict[str, Any]] = []
    institutions: list[FixtureInstitution] = []
    programs: list[FixtureProgram] = []
    sources: list[FixtureSource] = []
    evidence: list[FixtureEvidence] = []
    requirements: list[FixtureRequirement] = []
    intakes: list[FixtureIntake] = []


def load_fixture(path: Path | None = None) -> DemoFixture:
    """Read + validate the captured fixture; raise a clear AppError if unusable."""
    fixture_path = path or FIXTURE_PATH
    if not fixture_path.exists():
        raise AppError(
            503,
            "DEMO_FIXTURE_MISSING",
            f"Demo fixture not found at {fixture_path}. Run scripts/capture_example.py first.",
        )
    try:
        raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AppError(503, "DEMO_FIXTURE_INVALID", f"Demo fixture is not valid JSON: {exc}") from exc
    try:
        return DemoFixture.model_validate(raw)
    except ValidationError as exc:
        raise AppError(503, "DEMO_FIXTURE_INVALID", f"Demo fixture failed validation: {exc}") from exc


def _utc(value: str | None) -> datetime | None:
    """ISO string -> timezone-aware datetime (None stays None)."""
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _date(value: str | None) -> date | None:
    if value is None:
        return None
    return date.fromisoformat(value)


def _uuid(value: str) -> UUID:
    """Fixture ids are captured primary keys stored as strings."""
    return UUID(value)


async def _find_institution(session: AsyncSession, item: FixtureInstitution) -> Institution | None:
    rows = (
        await session.execute(
            select(Institution)
            .where(Institution.normalized_name == item.normalized_name)
            .order_by(Institution.id)
        )
    ).scalars().all()
    for row in rows:
        if row.country_code == item.country_code:
            return row
    # Name matches exist but no country agrees (countries are often NULL):
    # the name is still the same institution for replay purposes.
    return rows[0] if rows else None


async def _upsert_institution(
    session: AsyncSession, item: FixtureInstitution
) -> tuple[UUID, bool]:
    row = await _find_institution(session, item)
    if row is not None:
        row.canonical_name = item.canonical_name
        row.city = item.city
        row.website_url = item.website_url
        row.domain = item.domain
        row.institution_type = item.institution_type
        row.authority_score = item.authority_score
        if item.country_code is not None:
            row.country_code = item.country_code
        return row.id, False
    row = Institution(
        canonical_name=item.canonical_name,
        normalized_name=item.normalized_name,
        country_code=item.country_code,
        city=item.city,
        website_url=item.website_url,
        domain=item.domain,
        institution_type=item.institution_type,
        authority_score=item.authority_score,
    )
    session.add(row)
    await session.flush()
    return row.id, True


async def _find_program(
    session: AsyncSession, item: FixtureProgram, institution_id: UUID
) -> Program | None:
    row = (
        await session.execute(
            select(Program)
            .where(
                Program.institution_id == institution_id,
                Program.normalized_name == item.normalized_name,
            )
            .order_by(Program.id)
        )
    ).scalars().first()
    if row is not None:
        return row
    # Replay may resolve the fixture institution to a different duplicate row
    # than the original run used; the normalized program name still identifies it.
    return (
        await session.execute(
            select(Program)
            .where(Program.normalized_name == item.normalized_name)
            .order_by(Program.id)
        )
    ).scalars().first()


async def _upsert_program(
    session: AsyncSession, item: FixtureProgram, institution_id: UUID
) -> tuple[UUID, bool]:
    row = await _find_program(session, item, institution_id)
    if row is not None:
        row.canonical_name = item.canonical_name
        row.degree_type = item.degree_type
        row.field_of_study = item.field_of_study
        row.specialization = item.specialization
        row.city = item.city
        row.language = item.language
        row.official_url = item.official_url
        row.duration_months = item.duration_months
        row.tuition_amount = item.tuition_amount
        row.tuition_currency = item.tuition_currency
        row.extra = item.extra
        row.last_verified_at = _utc(item.last_verified_at)
        row.active = item.active
        if item.country_code is not None:
            row.country_code = item.country_code
        return row.id, False
    row = Program(
        institution_id=institution_id,
        canonical_name=item.canonical_name,
        normalized_name=item.normalized_name,
        degree_type=item.degree_type,
        field_of_study=item.field_of_study,
        specialization=item.specialization,
        city=item.city,
        country_code=item.country_code,
        language=item.language,
        official_url=item.official_url,
        duration_months=item.duration_months,
        tuition_amount=item.tuition_amount,
        tuition_currency=item.tuition_currency,
        extra=item.extra,
        last_verified_at=_utc(item.last_verified_at),
        active=item.active,
    )
    session.add(row)
    await session.flush()
    return row.id, True


async def _upsert_source(session: AsyncSession, item: FixtureSource) -> tuple[UUID, bool]:
    row = (
        await session.execute(select(Source).where(Source.canonical_url == item.canonical_url))
    ).scalars().first()
    if row is not None:
        row.url = item.url
        row.domain = item.domain
        row.title = item.title
        row.source_authority = item.source_authority
        row.publisher = item.publisher
        row.source_type = item.source_type
        return row.id, False
    row = Source(
        url=item.url,
        canonical_url=item.canonical_url,
        domain=item.domain,
        title=item.title,
        source_authority=item.source_authority,
        publisher=item.publisher,
        country_code=item.country_code,
        source_type=item.source_type,
    )
    session.add(row)
    await session.flush()
    return row.id, True


def _evidence_hash(item: FixtureEvidence) -> str:
    if item.content_hash:
        return item.content_hash
    stable = json.dumps(
        {
            "source_ref": item.source_ref,
            "claim_type": item.claim_type,
            "subject_ref": item.subject_ref,
            "normalized_claim": item.normalized_claim,
            "claim": item.claim,
            "extracted_value": item.extracted_value,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(stable.encode()).hexdigest()


async def _upsert_evidence(
    session: AsyncSession, item: FixtureEvidence, source_id: UUID, subject_id: UUID | None
) -> tuple[UUID, bool]:
    content_hash = _evidence_hash(item)
    # Evidence has no unique constraint, so the captured row's own primary key
    # is its identity: replaying into the database the fixture was captured
    # from updates those rows in place, and a fresh database gets them with
    # their original ids. (content_hash + claim is NOT unique: the captured run
    # holds distinct rows sharing both, so it cannot be the key.)
    row = (
        await session.execute(select(Evidence).where(Evidence.id == _uuid(item.id)))
    ).scalars().first()
    retrieved_at = datetime.fromisoformat(item.retrieved_at)
    if row is not None:
        row.source_id = source_id
        row.subject_type = item.subject_type
        row.subject_id = subject_id
        row.normalized_claim = item.normalized_claim
        row.snippet = item.snippet
        row.summary = item.summary
        row.extracted_value = item.extracted_value
        row.authority_score = item.authority_score
        row.confidence = item.confidence
        row.status = item.status
        row.retrieved_at = retrieved_at
        row.published_at = _utc(item.published_at)
        row.freshness_deadline = _utc(item.freshness_deadline)
        row.content_hash = content_hash
        row.extraction_model = item.extraction_model
        row.extraction_version = item.extraction_version
        return row.id, False
    row = Evidence(
        id=_uuid(item.id),
        source_id=source_id,
        search_result_id=None,
        claim_type=item.claim_type,
        subject_type=item.subject_type,
        subject_id=subject_id,
        claim=item.claim,
        normalized_claim=item.normalized_claim,
        snippet=item.snippet,
        summary=item.summary,
        extracted_value=item.extracted_value,
        authority_score=item.authority_score,
        confidence=item.confidence,
        status=item.status,
        retrieved_at=retrieved_at,
        published_at=_utc(item.published_at),
        freshness_deadline=_utc(item.freshness_deadline),
        content_hash=content_hash,
        conflict_group_id=None,
        extraction_model=item.extraction_model,
        extraction_version=item.extraction_version,
    )
    session.add(row)
    await session.flush()
    return row.id, True


async def _upsert_requirement(
    session: AsyncSession, item: FixtureRequirement, program_id: UUID
) -> tuple[UUID, bool]:
    row = (
        await session.execute(
            select(Requirement)
            .where(
                Requirement.program_id == program_id,
                Requirement.normalized_key == item.normalized_key,
            )
            .order_by(Requirement.id)
        )
    ).scalars().first()
    if row is not None:
        row.requirement_type = item.requirement_type
        row.title = item.title
        row.operator = item.operator
        row.value = item.value
        row.mandatory = item.mandatory
        row.applies_to = item.applies_to
        row.status = item.status
        row.last_verified_at = _utc(item.last_verified_at)
        return row.id, False
    row = Requirement(
        program_id=program_id,
        requirement_type=item.requirement_type,
        title=item.title,
        normalized_key=item.normalized_key,
        operator=item.operator,
        value=item.value,
        mandatory=item.mandatory,
        applies_to=item.applies_to,
        status=item.status,
        last_verified_at=_utc(item.last_verified_at),
    )
    session.add(row)
    await session.flush()
    return row.id, True


async def _upsert_intake(
    session: AsyncSession, item: FixtureIntake, program_id: UUID
) -> tuple[UUID, bool]:
    row = (
        await session.execute(
            select(Intake)
            .where(
                Intake.program_id == program_id,
                Intake.intake_label == item.intake_label,
                Intake.intake_year == item.intake_year,
            )
            .order_by(Intake.id)
        )
    ).scalars().first()
    if row is not None:
        row.start_date = _date(item.start_date)
        row.application_deadline = _date(item.application_deadline)
        row.deadline_type = item.deadline_type
        row.status = item.status
        return row.id, False
    row = Intake(
        program_id=program_id,
        intake_label=item.intake_label,
        intake_year=item.intake_year,
        start_date=_date(item.start_date),
        application_deadline=_date(item.application_deadline),
        deadline_type=item.deadline_type,
        status=item.status,
    )
    session.add(row)
    await session.flush()
    return row.id, True


def _missing_ref(kind: str, ref: str) -> AppError:
    return AppError(
        503, "DEMO_FIXTURE_INVALID", f"Demo fixture {kind} reference '{ref}' does not resolve"
    )


async def apply_fixture(session: AsyncSession, fixture: DemoFixture) -> dict[str, Any]:
    """Idempotently upsert every fixture row; returns replay counts for step output."""
    created = {
        "institutions": 0,
        "programs": 0,
        "sources": 0,
        "evidence": 0,
        "requirements": 0,
        "intakes": 0,
    }

    institution_ids: dict[str, UUID] = {}
    for inst in fixture.institutions:
        row_id, was_created = await _upsert_institution(session, inst)
        institution_ids[inst.id] = row_id
        created["institutions"] += int(was_created)
    await session.flush()

    program_ids: dict[str, UUID] = {}
    for prog in fixture.programs:
        institution_id = institution_ids.get(prog.institution_ref)
        if institution_id is None:
            raise _missing_ref("institution", prog.institution_ref)
        row_id, was_created = await _upsert_program(session, prog, institution_id)
        program_ids[prog.id] = row_id
        created["programs"] += int(was_created)
    await session.flush()

    source_ids: dict[str, UUID] = {}
    for src in fixture.sources:
        row_id, was_created = await _upsert_source(session, src)
        source_ids[src.id] = row_id
        created["sources"] += int(was_created)
    await session.flush()

    for ev in fixture.evidence:
        source_id = source_ids.get(ev.source_ref)
        if source_id is None:
            raise _missing_ref("source", ev.source_ref)
        subject_id: UUID | None = None
        if ev.subject_ref is not None:
            subject_id = program_ids.get(ev.subject_ref)
            if subject_id is None:
                raise _missing_ref("program(subject)", ev.subject_ref)
        _, was_created = await _upsert_evidence(session, ev, source_id, subject_id)
        created["evidence"] += int(was_created)
    await session.flush()

    for req in fixture.requirements:
        program_id = program_ids.get(req.program_ref)
        if program_id is None:
            raise _missing_ref("program", req.program_ref)
        _, was_created = await _upsert_requirement(session, req, program_id)
        created["requirements"] += int(was_created)

    for intake in fixture.intakes:
        program_id = program_ids.get(intake.program_ref)
        if program_id is None:
            raise _missing_ref("program", intake.program_ref)
        _, was_created = await _upsert_intake(session, intake, program_id)
        created["intakes"] += int(was_created)
    await session.flush()

    return {
        "fixture": True,
        "source_run_id": fixture.meta.source_run_id,
        "institutions": len(fixture.institutions),
        "programs": len(fixture.programs),
        "sources": len(fixture.sources),
        # A replay performs no live searches: it re-stores the sources the
        # captured run already found (frontend shows "replayed N sources").
        "results_stored": len(fixture.sources),
        "evidence": len(fixture.evidence),
        "requirements": len(fixture.requirements),
        "intakes": len(fixture.intakes),
        "searches_run": 0,
        "created": created,
    }
