import pytest

from app.services.evidence.llm_extract import KNOWN_CLAIM_KEYS, LLMClaims, extract_claims
from app.services.llm import LLMError


class _FailingProvider:
    async def generate_structured(self, input, schema, model_config=None):  # noqa: ANN001
        raise LLMError("no key")


class _GoodProvider:
    async def generate_structured(self, input, schema, model_config=None):  # noqa: ANN001
        return schema.model_validate(
            {
                "claims": [
                    {
                        "claim_type": "language",
                        "normalized_key": "ielts_overall_min",
                        "value": {"min": 6.5, "test": "IELTS"},
                        "claim": "Page states IELTS 6.5 overall required.",
                        "confidence": "HIGH",
                    }
                ]
            }
        )


@pytest.mark.asyncio
async def test_extraction_falls_back_to_none_on_llm_failure() -> None:
    out = await extract_claims(
        _FailingProvider(), title="MSc AI", snippet="IELTS 6.5 required", domain="uni.example"
    )
    assert out is None


@pytest.mark.asyncio
async def test_extraction_returns_validated_claims() -> None:
    out = await extract_claims(
        _GoodProvider(), title="MSc AI", snippet="IELTS 6.5 required", domain="uni.example"
    )
    assert out is not None
    assert len(out.claims) == 1
    assert out.claims[0].normalized_key == "ielts_overall_min"


@pytest.mark.asyncio
async def test_extraction_skips_when_no_text() -> None:
    out = await extract_claims(_GoodProvider(), title=None, snippet=None, domain=None)
    assert out is None


@pytest.mark.asyncio
async def test_extraction_preserves_program_profile() -> None:
    """The claim-truncation path must not drop the optional program_profile —
    it is the model's only route to Program enrichment."""

    class _ProfileProvider:
        async def generate_structured(self, input, schema, model_config=None):  # noqa: ANN001
            return schema.model_validate(
                {
                    "claims": [
                        {
                            "claim_type": "academic",
                            "normalized_key": "cgpa_min",
                            "value": {"min": 3.0},
                            "claim": "Page states CGPA 3.0 minimum.",
                            "confidence": "HIGH",
                        }
                    ],
                    "program_profile": {"degree_type": "M.Sc.", "language": "English"},
                }
            )

    out = await extract_claims(
        _ProfileProvider(), title="MSc AI", snippet="M.Sc. taught in English, CGPA 3.0", domain="uni.example"
    )
    assert out is not None
    assert out.program_profile is not None
    assert out.program_profile.degree_type == "M.Sc."
    assert out.program_profile.language == "English"
    assert len(out.claims) == 1


def test_known_keys_have_requirement_mapping() -> None:
    from app.services.evidence.llm_extract import KEY_OPERATOR, KEY_TO_REQUIREMENT_TYPE

    assert KNOWN_CLAIM_KEYS == set(KEY_TO_REQUIREMENT_TYPE) == set(KEY_OPERATOR)


def test_llmclaims_rejects_garbage() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        LLMClaims.model_validate({"claims": "not-a-list"})


def test_llmclaims_rejects_flat_model_response() -> None:
    """Models sometimes answer with a flat dict; the missing wrapper must fail."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        LLMClaims.model_validate({"ielts_overall_min": 6.5})
