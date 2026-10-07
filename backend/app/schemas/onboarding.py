from typing import Any

from pydantic import BaseModel


class OnboardingField(BaseModel):
    key: str
    question: str
    explanation: str
    example: str | None = None
    why_we_ask: str
    input_type: str
    options: list[str] = []
    required: bool = False
    skippable: bool = True


class OnboardingStep(BaseModel):
    id: str
    title: str
    fields: list[OnboardingField]


class OnboardingSchema(BaseModel):
    version: str
    steps: list[OnboardingStep]


class OnboardingAnswersIn(BaseModel):
    answers: dict[str, Any]


class OnboardingProgressOut(BaseModel):
    completion_percent: float
    answered_keys: list[str]
    missing_required_keys: list[str]
