from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ProfileBase(BaseModel):
    current_degree: str | None = None
    field_of_study: str | None = None
    institution_name: str | None = None
    institution_country_code: str | None = Field(default=None, min_length=2, max_length=2)
    graduation_year: int | None = None
    cgpa: Decimal | None = None
    cgpa_scale: Decimal | None = None
    percentage: Decimal | None = None
    backlogs: int | None = Field(default=None, ge=0)
    total_experience_months: int | None = Field(default=None, ge=0)
    budget_currency: str | None = Field(default=None, min_length=3, max_length=3)
    total_budget_amount: Decimal | None = None
    annual_budget_amount: Decimal | None = None
    tuition_budget_amount: Decimal | None = None
    scholarship_dependence: bool | None = None
    career_goal: str | None = None


class ProfileCreate(ProfileBase):
    email: str | None = None
    full_name: str | None = None


class ProfileUpdate(ProfileBase):
    pass


class ProfileOut(ProfileBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    user_id: UUID
    profile_completion: Decimal | None
    onboarding_version: str


class CompletionOut(BaseModel):
    profile_completion: float
    missing_fields: list[str]


class ValidationIssue(BaseModel):
    field: str
    message: str


class ValidationOut(BaseModel):
    valid: bool
    issues: list[ValidationIssue]


class PreferencesOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    preferred_countries: list[Any] = []
    excluded_countries: list[Any] = []
    preferred_cities: list[Any] = []
    preferred_degree_types: list[Any] = []
    preferred_fields: list[Any] = []
    target_intakes: list[Any] = []
    preferred_language: str | None = None
    research_preference: str | None = None
    career_market_importance: Decimal | None = None
    max_distance_preference: str | None = None


class TestScoreIn(BaseModel):
    test_type: str
    overall_score: Decimal | None = None
    section_scores: dict[str, Any] = {}
    test_date: date | None = None
    expiry_date: date | None = None
