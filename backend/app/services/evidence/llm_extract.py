"""LLM-based claim extraction from search results, with honest fallback.

Every claim must be grounded in the provided snippet/title; the model is never
asked to guess. Failures degrade to zero claims (UNKNOWN), never to invented data.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from app.services.llm import LLMProvider

logger = logging.getLogger(__name__)

MAX_CLAIMS = 4
# Results per run sent to LLM extraction. A deep run stores ~150 results;
# 20 keeps meaningful coverage of the program pages (12 slots, 4 discovery,
# 2 funding, 2 policy/news/career) while staying well inside free-tier LLM
# budgets. Purpose shares live in research.orchestrator._EXTRACTION_SHARES.
MAX_RESULTS_PER_RUN = 20

# Claim types the extraction schema accepts. `scholarship` claims are stored as
# evidence only (no requirement_type home in the requirements model).
CLAIM_TYPES = (
    "language",
    "academic",
    "deadline",
    "tuition",
    "prerequisite",
    "fee",
    "scholarship",
)

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
    claim_type: str = Field(
        description="One of: " + ", ".join(CLAIM_TYPES)
    )
    normalized_key: str = Field(description="One of the known keys when applicable, else a snake_case key")
    value: dict[str, Any] = Field(default_factory=dict)
    claim: str = Field(description="One sentence grounded in the source text")
    confidence: str = Field(default="LOW", description="HIGH, MEDIUM or LOW")


class LLMProgramProfile(BaseModel):
    """Optional program facts observed in the source text.

    Every field is optional and MUST be null unless the snippet states it —
    null means UNKNOWN. The orchestrator upserts only non-null fields.
    """

    country_code: str | None = Field(default=None, description="ISO 3166 alpha-2, only if stated")
    degree_type: str | None = Field(default=None, description="e.g. M.Sc., M.A., MBA, only if stated")
    field_of_study: str | None = Field(default=None, description="only if stated")
    official_url: str | None = Field(default=None, description="official program page URL, only if stated")
    language: str | None = Field(default=None, description="language of instruction, only if stated")
    duration_months: int | None = Field(
        default=None, description="program duration in months, only if stated"
    )
    tuition_amount: float | None = Field(default=None, description="tuition figure, only if stated")
    tuition_currency: str | None = Field(default=None, description="ISO 4217 currency, only if stated")

    @classmethod
    def example(cls) -> dict[str, Any]:
        return {
            "country_code": None,
            "degree_type": "M.Sc.",
            "field_of_study": None,
            "official_url": None,
            "language": "English",
            "duration_months": None,
            "tuition_amount": None,
            "tuition_currency": None,
        }


class LLMClaims(BaseModel):
    # Required (no default): a response missing the wrapper must fail validation,
    # so the caller falls back to the next endpoint instead of accepting [] silently.
    claims: list[LLMClaim]
    program_profile: LLMProgramProfile | None = None

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
                },
                {
                    "claim_type": "scholarship",
                    "normalized_key": "scholarship_availability",
                    "value": {"note": "page lists an international scholarship"},
                    "claim": "Program page lists an international scholarship.",
                    "confidence": "MEDIUM",
                },
            ],
            "program_profile": LLMProgramProfile.example(),
        }

    @property
    def max_confidence(self) -> str:
        order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
        if not self.claims:
            return "LOW"
        return max((c.confidence for c in self.claims), key=lambda c: order.get(c, 0))


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
        "claim_types": list(CLAIM_TYPES),
        "title": title or "",
        "snippet": snippet or "",
        "domain": domain or "",
        "max_claims": MAX_CLAIMS,
        "required_top_level_key": "claims",
        "program_profile_fields": list(LLMProgramProfile.model_fields),
        "program_profile_rule": (
            "Include program_profile whenever the text states ANY field "
            "(degree_type from the title, language, official_url, duration, "
            "tuition); leave the rest null. Otherwise return null. Null means "
            "UNKNOWN - never guess."
        ),
        "deadline_value_format": (
            "application_deadline values must carry an ISO date: "
            '{"date": "YYYY-MM-DD"} for a single closing date, or '
            '{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"} for an application '
            "window (end = the effective deadline). Never invent a date the "
            "text does not state; prose or term-only deadlines use a note."
        ),
        "example_output": LLMClaims.example(),
    }
    try:
        result = await provider.generate_structured(input_payload, LLMClaims)
    except Exception as exc:  # noqa: BLE001 - extraction must degrade to "no claims", never fail the run
        logger.info("LLM extraction unavailable: %r", exc)
        return None
    # Truncate claims but PRESERVE the optional program_profile: reconstructing
    # the model here silently dropped it, so Program enrichment never ran live.
    return LLMClaims(claims=result.claims[:MAX_CLAIMS], program_profile=result.program_profile)
