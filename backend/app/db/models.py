"""SQLAlchemy 2.0 models mirroring database/schema.sql (PostgreSQL)."""

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class UserRole(enum.StrEnum):
    STUDENT = "STUDENT"
    ADMIN = "ADMIN"


class RequirementStatus(enum.StrEnum):
    SATISFIED = "SATISFIED"
    PARTIAL = "PARTIAL"
    NOT_SATISFIED = "NOT_SATISFIED"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    CONFLICTING = "CONFLICTING"
    NEEDS_VERIFICATION = "NEEDS_VERIFICATION"


class RiskSeverity(enum.StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class RiskStatus(enum.StrEnum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


class SourceAuthority(enum.StrEnum):
    OFFICIAL_UNIVERSITY = "OFFICIAL_UNIVERSITY"
    OFFICIAL_GOVERNMENT = "OFFICIAL_GOVERNMENT"
    OFFICIAL_ORGANIZATION = "OFFICIAL_ORGANIZATION"
    ACCREDITED_BODY = "ACCREDITED_BODY"
    CREDIBLE_SECONDARY = "CREDIBLE_SECONDARY"
    NEWS = "NEWS"
    FORUM_SOCIAL = "FORUM_SOCIAL"
    UNKNOWN = "UNKNOWN"


class EvidenceStatus(enum.StrEnum):
    CURRENT = "CURRENT"
    STALE = "STALE"
    CONFLICTING = "CONFLICTING"
    UNAVAILABLE = "UNAVAILABLE"


class ConfidenceLevel(enum.StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ProgramCategory(enum.StrEnum):
    REACH = "REACH"
    TARGET = "TARGET"
    LOWER_RISK = "LOWER_RISK"


class TaskStatus(enum.StrEnum):
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"
    BLOCKED = "BLOCKED"
    SKIPPED = "SKIPPED"


class RunStatus(enum.StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


def _ts() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(Text, unique=True)
    full_name: Mapped[str | None] = mapped_column(Text)
    password_hash: Mapped[str | None] = mapped_column(Text)  # NULL = anonymous/demo user
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", create_type=False), server_default="STUDENT"
    )
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_user_created", "user_id", "created_at"),)
    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(Text)  # e.g. DEADLINE_CHANGED, RESEARCH_COMPLETE
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(Text)  # in-app route, e.g. /monitor
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    email_status: Mapped[str] = mapped_column(Text, server_default="PENDING")  # PENDING|SENT|SKIPPED|FAILED
    email_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _ts()


class StudentProfile(Base):
    __tablename__ = "student_profiles"
    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    current_degree: Mapped[str | None] = mapped_column(Text)
    field_of_study: Mapped[str | None] = mapped_column(Text)
    institution_name: Mapped[str | None] = mapped_column(Text)
    institution_country_code: Mapped[str | None] = mapped_column(String(2))
    graduation_year: Mapped[int | None] = mapped_column(SmallInteger)
    cgpa: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    cgpa_scale: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    percentage: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    backlogs: Mapped[int | None] = mapped_column(Integer, server_default="0")
    total_experience_months: Mapped[int | None] = mapped_column(Integer, server_default="0")
    budget_currency: Mapped[str | None] = mapped_column(String(3), server_default="INR")
    total_budget_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    annual_budget_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    tuition_budget_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    scholarship_dependence: Mapped[bool | None] = mapped_column(Boolean, server_default="false")
    career_goal: Mapped[str | None] = mapped_column(Text)
    profile_completion: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), server_default="0")
    onboarding_version: Mapped[str] = mapped_column(Text, server_default="v1")
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class ProfilePreference(Base):
    __tablename__ = "profile_preferences"
    id: Mapped[uuid.UUID] = _uuid_pk()
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("student_profiles.id", ondelete="CASCADE"))
    preferred_countries: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    excluded_countries: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    preferred_cities: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    preferred_degree_types: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    preferred_fields: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    target_intakes: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    preferred_language: Mapped[str | None] = mapped_column(Text)
    research_preference: Mapped[str | None] = mapped_column(Text)
    career_market_importance: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    max_distance_preference: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class EducationRecord(Base):
    __tablename__ = "education_records"
    id: Mapped[uuid.UUID] = _uuid_pk()
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("student_profiles.id", ondelete="CASCADE"))
    institution_name: Mapped[str] = mapped_column(Text)
    country_code: Mapped[str | None] = mapped_column(String(2))
    degree: Mapped[str] = mapped_column(Text)
    field_of_study: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    grade_value: Mapped[Decimal | None] = mapped_column(Numeric(8, 3))
    grade_scale: Mapped[Decimal | None] = mapped_column(Numeric(8, 3))
    grade_type: Mapped[str | None] = mapped_column(Text)
    is_current: Mapped[bool | None] = mapped_column(Boolean, server_default="false")
    created_at: Mapped[datetime] = _ts()


class EducationSubject(Base):
    __tablename__ = "education_subjects"
    id: Mapped[uuid.UUID] = _uuid_pk()
    education_record_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("education_records.id", ondelete="CASCADE")
    )
    subject_name: Mapped[str] = mapped_column(Text)
    credits: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    grade_value: Mapped[str | None] = mapped_column(Text)
    normalized_subject: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _ts()


class TestScore(Base):
    __tablename__ = "test_scores"
    id: Mapped[uuid.UUID] = _uuid_pk()
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("student_profiles.id", ondelete="CASCADE"))
    test_type: Mapped[str] = mapped_column(Text)
    overall_score: Mapped[Decimal | None] = mapped_column(Numeric(8, 3))
    section_scores: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    test_date: Mapped[date | None] = mapped_column(Date)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str | None] = mapped_column(Text, server_default="VALID")
    evidence_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = _ts()


class Experience(Base):
    __tablename__ = "experiences"
    id: Mapped[uuid.UUID] = _uuid_pk()
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("student_profiles.id", ondelete="CASCADE"))
    experience_type: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    organization: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    extra: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, server_default="{}")
    created_at: Mapped[datetime] = _ts()


class Skill(Base):
    __tablename__ = "skills"
    __table_args__ = (UniqueConstraint("profile_id", "skill_name"),)
    id: Mapped[uuid.UUID] = _uuid_pk()
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("student_profiles.id", ondelete="CASCADE"))
    skill_name: Mapped[str] = mapped_column(Text)
    proficiency: Mapped[str | None] = mapped_column(Text)
    months_experience: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _ts()


class Country(Base):
    __tablename__ = "countries"
    code: Mapped[str] = mapped_column(String(2), primary_key=True)
    name: Mapped[str] = mapped_column(Text, unique=True)
    region: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str | None] = mapped_column(String(3))
    extra: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, server_default="{}")


class Institution(Base):
    __tablename__ = "institutions"
    __table_args__ = (UniqueConstraint("normalized_name", "country_code"),)
    id: Mapped[uuid.UUID] = _uuid_pk()
    canonical_name: Mapped[str] = mapped_column(Text)
    normalized_name: Mapped[str] = mapped_column(Text)
    country_code: Mapped[str | None] = mapped_column(String(2), ForeignKey("countries.code"))
    city: Mapped[str | None] = mapped_column(Text)
    website_url: Mapped[str | None] = mapped_column(Text)
    domain: Mapped[str | None] = mapped_column(Text)
    institution_type: Mapped[str | None] = mapped_column(Text)
    authority_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), server_default="0")
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class Program(Base):
    __tablename__ = "programs"
    __table_args__ = (
        UniqueConstraint("institution_id", "normalized_name"),
        Index("idx_programs_country_field", "country_code", "field_of_study"),
        Index("idx_programs_normalized_name", "normalized_name"),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    institution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("institutions.id"))
    institution: Mapped["Institution"] = relationship("Institution")
    canonical_name: Mapped[str] = mapped_column(Text)
    normalized_name: Mapped[str] = mapped_column(Text)
    degree_type: Mapped[str | None] = mapped_column(Text)
    field_of_study: Mapped[str | None] = mapped_column(Text)
    specialization: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(Text)
    country_code: Mapped[str | None] = mapped_column(String(2), ForeignKey("countries.code"))
    language: Mapped[str | None] = mapped_column(Text)
    official_url: Mapped[str | None] = mapped_column(Text)
    duration_months: Mapped[int | None] = mapped_column(Integer)
    tuition_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    tuition_currency: Mapped[str | None] = mapped_column(String(3))
    extra: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, server_default="{}")
    first_seen_at: Mapped[datetime] = _ts()
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    updated_at: Mapped[datetime] = _ts()  # kept current by the programs_updated_at trigger


class Intake(Base):
    __tablename__ = "intakes"
    __table_args__ = (UniqueConstraint("program_id", "intake_label", "intake_year"),)
    id: Mapped[uuid.UUID] = _uuid_pk()
    program_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("programs.id", ondelete="CASCADE"))
    intake_label: Mapped[str] = mapped_column(Text)
    intake_year: Mapped[int] = mapped_column(SmallInteger)
    start_date: Mapped[date | None] = mapped_column(Date)
    application_deadline: Mapped[date | None] = mapped_column(Date)
    deadline_type: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text, server_default="OPEN")
    evidence_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = _ts()


class Requirement(Base):
    __tablename__ = "requirements"
    __table_args__ = (
        UniqueConstraint("program_id", "normalized_key"),
        Index("idx_requirements_program", "program_id"),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    program_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("programs.id", ondelete="CASCADE"))
    requirement_type: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    normalized_key: Mapped[str] = mapped_column(Text)
    operator: Mapped[str | None] = mapped_column(Text)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB)
    mandatory: Mapped[bool] = mapped_column(Boolean, server_default="true")
    applies_to: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    status: Mapped[RequirementStatus] = mapped_column(
        Enum(RequirementStatus, name="requirement_status", create_type=False), server_default="UNKNOWN"
    )
    first_seen_at: Mapped[datetime] = _ts()
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RequirementVersion(Base):
    __tablename__ = "requirement_versions"
    id: Mapped[uuid.UUID] = _uuid_pk()
    requirement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("requirements.id", ondelete="CASCADE"))
    value: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[RequirementStatus] = mapped_column(
        Enum(RequirementStatus, name="requirement_status", create_type=False)
    )
    valid_from: Mapped[datetime] = _ts()
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    change_reason: Mapped[str | None] = mapped_column(Text)
    evidence_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[uuid.UUID] = _uuid_pk()
    url: Mapped[str] = mapped_column(Text)
    canonical_url: Mapped[str] = mapped_column(Text, unique=True)
    domain: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    source_authority: Mapped[SourceAuthority] = mapped_column(
        Enum(SourceAuthority, name="source_authority", create_type=False), server_default="UNKNOWN"
    )
    publisher: Mapped[str | None] = mapped_column(Text)
    country_code: Mapped[str | None] = mapped_column(String(2))
    source_type: Mapped[str | None] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = _ts()
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SearchRun(Base):
    __tablename__ = "search_runs"
    __table_args__ = (Index("idx_search_runs_user", "user_id", "requested_at"),)
    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    engine: Mapped[str] = mapped_column(Text)
    query: Mapped[str] = mapped_column(Text)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    serpapi_search_id: Mapped[str | None] = mapped_column(Text)
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, name="run_status", create_type=False), server_default="RUNNING"
    )
    requested_at: Mapped[datetime] = _ts()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    result_count: Mapped[int | None] = mapped_column(Integer)
    cache_hit: Mapped[bool | None] = mapped_column(Boolean, server_default="false")
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)


class SearchResult(Base):
    __tablename__ = "search_results"
    id: Mapped[uuid.UUID] = _uuid_pk()
    search_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("search_runs.id", ondelete="CASCADE"))
    source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sources.id"))
    position: Mapped[int | None] = mapped_column(Integer)
    result_type: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    snippet: Mapped[str | None] = mapped_column(Text)
    displayed_url: Mapped[str | None] = mapped_column(Text)
    result_url: Mapped[str | None] = mapped_column(Text)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    content_hash: Mapped[str | None] = mapped_column(Text)
    retrieved_at: Mapped[datetime] = _ts()


class Evidence(Base):
    __tablename__ = "evidence"
    __table_args__ = (
        Index("idx_evidence_subject", "subject_type", "subject_id"),
        Index("idx_evidence_retrieved", "retrieved_at"),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"))
    search_result_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("search_results.id"))
    claim_type: Mapped[str] = mapped_column(Text)
    subject_type: Mapped[str] = mapped_column(Text)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    claim: Mapped[str] = mapped_column(Text)
    normalized_claim: Mapped[str | None] = mapped_column(Text)
    snippet: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    extracted_value: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    authority_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), server_default="0")
    confidence: Mapped[ConfidenceLevel] = mapped_column(
        Enum(ConfidenceLevel, name="confidence_level", create_type=False), server_default="LOW"
    )
    status: Mapped[EvidenceStatus] = mapped_column(
        Enum(EvidenceStatus, name="evidence_status", create_type=False), server_default="CURRENT"
    )
    retrieved_at: Mapped[datetime] = _ts()
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    freshness_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str | None] = mapped_column(Text)
    conflict_group_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    extraction_model: Mapped[str | None] = mapped_column(Text)
    extraction_version: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _ts()


class EvidenceConflict(Base):
    __tablename__ = "evidence_conflicts"
    id: Mapped[uuid.UUID] = _uuid_pk()
    conflict_key: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    resolution_status: Mapped[str] = mapped_column(Text, server_default="UNRESOLVED")
    preferred_evidence_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("evidence.id"))
    resolution_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _ts()
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvidenceConflictMember(Base):
    __tablename__ = "evidence_conflict_members"
    conflict_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence_conflicts.id", ondelete="CASCADE"), primary_key=True
    )
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence.id", ondelete="CASCADE"), primary_key=True
    )


class ResearchPlan(Base):
    __tablename__ = "research_plans"
    id: Mapped[uuid.UUID] = _uuid_pk()
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("student_profiles.id", ondelete="CASCADE"))
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, name="run_status", create_type=False), server_default="QUEUED"
    )
    requested_goal: Mapped[dict[str, Any]] = mapped_column(JSONB)
    planned_queries: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    mode: Mapped[str] = mapped_column(Text, server_default="live")  # live | demo
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _ts()


class ResearchPlanStep(Base):
    __tablename__ = "research_plan_steps"
    __table_args__ = (UniqueConstraint("research_plan_id", "step_key"),)
    id: Mapped[uuid.UUID] = _uuid_pk()
    research_plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_plans.id", ondelete="CASCADE")
    )
    step_key: Mapped[str] = mapped_column(Text)
    service_name: Mapped[str] = mapped_column(Text)
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, name="run_status", create_type=False), server_default="QUEUED"
    )
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    output: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    search_run_ids: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)


class FitAssessment(Base):
    __tablename__ = "fit_assessments"
    id: Mapped[uuid.UUID] = _uuid_pk()
    research_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("research_plans.id", ondelete="CASCADE")
    )
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("student_profiles.id", ondelete="CASCADE"))
    program_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("programs.id", ondelete="CASCADE"))
    academic_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    prerequisite_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    language_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    financial_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    career_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    timing_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    evidence_confidence_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    overall_score: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    scoring_version: Mapped[str] = mapped_column(Text)
    explanation: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _ts()


class FitDimensionEvidence(Base):
    __tablename__ = "fit_dimension_evidence"
    fit_assessment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fit_assessments.id", ondelete="CASCADE"), primary_key=True
    )
    dimension: Mapped[str] = mapped_column(Text, primary_key=True)
    evidence_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evidence.id"), primary_key=True)


class EligibilityAssessment(Base):
    __tablename__ = "eligibility_assessments"
    __table_args__ = (UniqueConstraint("fit_assessment_id", "requirement_id"),)
    id: Mapped[uuid.UUID] = _uuid_pk()
    fit_assessment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fit_assessments.id", ondelete="CASCADE")
    )
    requirement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("requirements.id"))
    status: Mapped[RequirementStatus] = mapped_column(
        Enum(RequirementStatus, name="requirement_status", create_type=False)
    )
    matched_value: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    expected_value: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    reason: Mapped[str] = mapped_column(Text)
    confidence: Mapped[ConfidenceLevel] = mapped_column(
        Enum(ConfidenceLevel, name="confidence_level", create_type=False)
    )
    created_at: Mapped[datetime] = _ts()


class EligibilityEvidence(Base):
    __tablename__ = "eligibility_evidence"
    eligibility_assessment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("eligibility_assessments.id", ondelete="CASCADE"), primary_key=True
    )
    evidence_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evidence.id"), primary_key=True)


class Risk(Base):
    __tablename__ = "risks"
    __table_args__ = (Index("idx_risks_profile_status", "profile_id", "status", "severity"),)
    id: Mapped[uuid.UUID] = _uuid_pk()
    fit_assessment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("fit_assessments.id", ondelete="CASCADE")
    )
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("student_profiles.id", ondelete="CASCADE"))
    program_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("programs.id", ondelete="CASCADE"))
    risk_type: Mapped[str] = mapped_column(Text)
    severity: Mapped[RiskSeverity] = mapped_column(
        Enum(RiskSeverity, name="risk_severity", create_type=False)
    )
    title: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    recommended_action: Mapped[str | None] = mapped_column(Text)
    status: Mapped[RiskStatus] = mapped_column(
        Enum(RiskStatus, name="risk_status", create_type=False), server_default="OPEN"
    )
    confidence: Mapped[ConfidenceLevel] = mapped_column(
        Enum(ConfidenceLevel, name="confidence_level", create_type=False), server_default="MEDIUM"
    )
    detected_at: Mapped[datetime] = _ts()
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RiskEvidence(Base):
    __tablename__ = "risk_evidence"
    risk_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("risks.id", ondelete="CASCADE"), primary_key=True)
    evidence_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evidence.id"), primary_key=True)


class StrategyRun(Base):
    __tablename__ = "strategy_runs"
    __table_args__ = (Index("idx_strategy_profile", "profile_id", "created_at"),)
    id: Mapped[uuid.UUID] = _uuid_pk()
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("student_profiles.id", ondelete="CASCADE"))
    research_plan_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("research_plans.id"))
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, name="run_status", create_type=False), server_default="RUNNING"
    )
    scoring_version: Mapped[str] = mapped_column(Text)
    strategy_version: Mapped[str] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    plan_health_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    created_at: Mapped[datetime] = _ts()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApplicationPlan(Base):
    __tablename__ = "application_plans"
    __table_args__ = (UniqueConstraint("strategy_run_id", "program_id"),)
    id: Mapped[uuid.UUID] = _uuid_pk()
    strategy_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("strategy_runs.id", ondelete="CASCADE"))
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("student_profiles.id", ondelete="CASCADE"))
    program_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("programs.id"))
    program: Mapped["Program"] = relationship("Program")
    category: Mapped[ProgramCategory] = mapped_column(
        Enum(ProgramCategory, name="program_category", create_type=False)
    )
    priority: Mapped[int] = mapped_column(Integer)
    fit_assessment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("fit_assessments.id"))
    rationale: Mapped[str] = mapped_column(Text)
    estimated_cost: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    next_deadline: Mapped[date | None] = mapped_column(Date)
    next_action: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text, server_default="PLANNED")
    created_at: Mapped[datetime] = _ts()


class RoadmapTask(Base):
    __tablename__ = "roadmap_tasks"
    __table_args__ = (Index("idx_tasks_profile_due", "profile_id", "due_date", "status"),)
    id: Mapped[uuid.UUID] = _uuid_pk()
    strategy_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("strategy_runs.id", ondelete="CASCADE"))
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("student_profiles.id", ondelete="CASCADE"))
    program_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("programs.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    task_type: Mapped[str] = mapped_column(Text)
    due_date: Mapped[date | None] = mapped_column(Date)
    priority: Mapped[int | None] = mapped_column(Integer, server_default="3")
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, name="task_status", create_type=False), server_default="TODO"
    )
    evidence_ids: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    created_at: Mapped[datetime] = _ts()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SavedProgram(Base):
    __tablename__ = "saved_programs"
    __table_args__ = (UniqueConstraint("profile_id", "program_id"),)
    id: Mapped[uuid.UUID] = _uuid_pk()
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("student_profiles.id", ondelete="CASCADE"))
    program_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("programs.id"))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _ts()


class MonitorSubscription(Base):
    __tablename__ = "monitor_subscriptions"
    __table_args__ = (
        Index("idx_monitor_next_check", "next_check_at", postgresql_where=text("enabled = TRUE")),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("student_profiles.id", ondelete="CASCADE"))
    program_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("programs.id", ondelete="CASCADE"))
    field_key: Mapped[str] = mapped_column(Text)
    frequency: Mapped[str] = mapped_column(Text, server_default="WEEKLY")
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="true")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _ts()


class MonitorSnapshot(Base):
    __tablename__ = "monitor_snapshots"
    id: Mapped[uuid.UUID] = _uuid_pk()
    subscription_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("monitor_subscriptions.id", ondelete="CASCADE")
    )
    field_key: Mapped[str] = mapped_column(Text)
    old_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    new_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    change_type: Mapped[str] = mapped_column(Text)
    evidence_ids: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    material_change: Mapped[bool] = mapped_column(Boolean, server_default="false")
    checked_at: Mapped[datetime] = _ts()


class CounterfactualRun(Base):
    __tablename__ = "counterfactual_runs"
    id: Mapped[uuid.UUID] = _uuid_pk()
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("student_profiles.id", ondelete="CASCADE"))
    base_strategy_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("strategy_runs.id"))
    scenario_name: Mapped[str] = mapped_column(Text)
    modified_profile: Mapped[dict[str, Any]] = mapped_column(JSONB)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    created_at: Mapped[datetime] = _ts()


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[uuid.UUID] = _uuid_pk()
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("student_profiles.id", ondelete="CASCADE"))
    document_type: Mapped[str] = mapped_column(Text)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, name="task_status", create_type=False), server_default="TODO"
    )
    expires_at: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _ts()
