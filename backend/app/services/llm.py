"""Structured-output LLM provider interface."""

from __future__ import annotations

from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from app.core.config import Settings

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    pass


class LLMProvider(Protocol):
    async def generate_structured(
        self, input: dict[str, Any], schema: type[T], model_config: dict[str, Any] | None = None
    ) -> T: ...


class NullLLMProvider:
    """Used when no LLM key is configured. Never invents data; always raises for extraction."""

    async def generate_structured(
        self, input: dict[str, Any], schema: type[T], model_config: dict[str, Any] | None = None
    ) -> T:
        raise LLMError("LLM provider not configured")


def get_llm_provider(settings: Settings) -> LLMProvider:
    if not settings.llm_api_key:
        return NullLLMProvider()
    # A real provider requires a vendor SDK; keep the seam explicit.
    return NullLLMProvider()


async def generate_with_repair(
    provider: LLMProvider,
    raw_generator: Any,  # async callable returning raw JSON-like dict
    schema: type[T],
    *,
    max_attempts: int = 2,
) -> T:
    last_error: Exception | None = None
    for _ in range(max_attempts):
        try:
            raw = await raw_generator()
            return schema.model_validate(raw)
        except (ValidationError, LLMError, ValueError) as exc:
            last_error = exc
    raise LLMError(f"LLM output failed validation after {max_attempts} attempts: {last_error}")
