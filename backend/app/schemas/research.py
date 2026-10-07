from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ResearchPlanCreate(BaseModel):
    intake_year: int | None = Field(default=None, ge=2020, le=2100)
    goal: dict[str, Any] = {}


class ResearchPlanOut(BaseModel):
    research_plan_id: UUID
    status: str


class ResearchRunCreate(BaseModel):
    intake_year: int | None = Field(default=None, ge=2020, le=2100)
    goal: dict[str, Any] = {}


class ResearchPlanDetail(BaseModel):
    id: UUID
    status: str
    planned_queries: list[Any]
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class ResearchStepOut(BaseModel):
    step_key: str
    service_name: str
    status: str
    error_message: str | None
    output: dict[str, Any]


class ResearchEventsOut(BaseModel):
    run_id: UUID
    steps: list[ResearchStepOut]
