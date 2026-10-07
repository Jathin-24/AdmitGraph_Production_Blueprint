"""LLM-based claim extraction from search results, with honest fallback.

Every claim must be grounded in the provided snippet/title; the model is never
asked to guess. Failures degrade to zero claims (UNKNOWN), never to invented data.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from app.services.llm import LLMError, LLMProvider

logger = logging.getLogger(__name__)

MAX_CLAIMS = 4
MAX_RESULTS_PER_RUN = 6

# Keys the deterministic evaluator understands; anything else is stored as
# evidence but does not create a Requirement row.
KNOWN_CLAIM_KEYS = {
    "ielts_overall_min",
    "academic_cgpa_min",
    "cgpa_min",
    "backlogs_max",
    "application_deadline",
    "tuition_max",
    "budget_min",
    "prerequisite_subjects",
}

KEY_TO_REQUIREMENT_TYPE = {
    "ielts_overall_min": "language",
    "academic_cgpa_min": "academic",
    "cgpa_min": "academic",
    "backlogs_max": "academic",
    "application_deadline": "deadline",
    "tuition_max": "tuition",
    "budget_min": "budget",
    "prerequisite_subjects": "prerequisite",
}

KEY_OPERATOR = {
    "ielts_overall_min": "gte",
    "academic_cgpa_min": "gte",
    "cgpa_min": "gte",
    "backlogs_max": "lte",
    "application_deadline": "eq",
    "tuition_max": "lte",
    "budget_min": "gte",
    "prerequisite_subjects": "in",
}


class LLMClaim(BaseModel):
    claim_type: str = Field(description="One of: language, academic, deadline, tuition, prerequisite, fee")
    normalized_key: str = Field(description="One of the known keys when applicable, else a snake_case key")
    value: dict[str, Any] = Field(default_factory=dict)
    claim: str = Field(description="One sentence grounded in the source text")
    confidence: str = Field(default="LOW", description="HIGH, MEDIUM or LOW")


class LLMClaims(BaseModel):
    # Required (no default): a response missing the wrapper must fail validation,
    # so the caller falls back to the next endpoint instead of accepting [] silently.
    claims: list[LLMClaim]

    @classmethod
    def example(cls) -> dict[str, Any]:
        return {
            "claims": [
                {
                    "claim_type": "language",
                    "normalized_key": "ielts_overall_min",
                    "value": {"min": 6.5, "test": "IELTS"},
                    "claim": "Program page states IELTS 6.5 overall required.",
                    "confidence": "HIGH",
                }
            ]
        }


async def extract_claims(
    provider: LLMProvider,
    *,
    title: str | None,
    snippet: str | None,
    domain: str | None,
    extraction_model: str | None = None,
) -> LLMClaims | None:
    """Returns validated claims or None on any failure (callers treat None as 'no claims')."""
    if not snippet and not title:
        return None
    input_payload = {
        "task": "extract_claims",
        "known_keys": sorted(KNOWN_CLAIM_KEYS),
        "title": title or "",
        "snippet": snippet or "",
        "domain": domain or "",
        "max_claims": MAX_CLAIMS,
        "required_top_level_key": "claims",
        "example_output": LLMClaims.example(),
    }
    try:
        result = await provider.generate_structured(input_payload, LLMClaims)
    except LLMError as exc:
        logger.info("LLM extraction unavailable: %s", exc)
        return None
    return LLMClaims(claims=result.claims[:MAX_CLAIMS])
