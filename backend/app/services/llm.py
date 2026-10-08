"""Structured-output LLM provider interface."""

from __future__ import annotations

import inspect
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from app.core.config import Settings

T = TypeVar("T", bound=BaseModel)

# Cap repair payloads so a huge invalid answer cannot blow the context window.
_MAX_INVALID_OUTPUT_CHARS = 4000
_MAX_SCHEMA_CHARS = 4000


class LLMError(Exception):
    pass


def build_repair_message(
    error: Exception, invalid_output: Any, schema: type[BaseModel]
) -> str:
    """Repair prompt: validation error + the invalid output + a schema reminder.

    Per MASTER_SPEC §15, invalid LLM output is retried with a repair prompt,
    then fails safely.
    """
    try:
        schema_json = schema.model_json_schema()
        import json as _json

        schema_text = _json.dumps(schema_json)
    except Exception:  # noqa: BLE001 - a schema that cannot serialize still repairs
        schema_text = str(getattr(schema, "__name__", schema))
    if len(schema_text) > _MAX_SCHEMA_CHARS:
        schema_text = schema_text[:_MAX_SCHEMA_CHARS] + "…(truncated)"
    if isinstance(invalid_output, str):
        invalid_text = invalid_output
    else:
        import json as _json

        try:
            invalid_text = _json.dumps(invalid_output)
        except (TypeError, ValueError):
            invalid_text = repr(invalid_output)
    if len(invalid_text) > _MAX_INVALID_OUTPUT_CHARS:
        invalid_text = invalid_text[:_MAX_INVALID_OUTPUT_CHARS] + "…(truncated)"
    return (
        "Your previous response failed schema validation. Fix it and reply with "
        "ONLY a single JSON object matching the schema.\n"
        f"Validation error:\n{error}\n\n"
        f"Invalid output you produced:\n{invalid_text}\n\n"
        f"Schema reminder (JSON Schema):\n{schema_text}"
    )


class OpenAICompatibleLLMProvider:
    """Works for OpenRouter and Groq (both OpenAI-compatible chat APIs)."""

    def __init__(self, endpoints: list[dict[str, str]]) -> None:
        self._endpoints = endpoints

    def _messages(
        self, prompt: str, repair: str | None
    ) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    "You are a structured extraction engine. Respond ONLY with a single JSON object "
                    "matching the requested schema. No prose, no markdown fences."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        if repair:
            messages.append({"role": "user", "content": repair})
        return messages

    async def generate_structured(
        self, input: dict[str, Any], schema: type[T], model_config: dict[str, Any] | None = None
    ) -> T:
        import json as _json
        import logging

        import httpx

        logger = logging.getLogger(__name__)
        last_error: Exception | None = None
        for endpoint in self._endpoints:
            prompt = _json.dumps(input)
            repair: str | None = None
            # One in-endpoint repair attempt on schema mismatch, then fall
            # through to the next configured endpoint (as before).
            for _attempt in range(2):
                data: Any = None
                try:
                    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as http:
                        response = await http.post(
                            f"{endpoint['base_url']}/chat/completions",
                            headers={"Authorization": f"Bearer {endpoint['api_key']}"},
                            json={
                                "model": endpoint["model"],
                                "messages": self._messages(prompt, repair),
                                "response_format": {"type": "json_object"},
                            },
                        )
                    if response.status_code != 200:
                        raise LLMError(
                            f"{endpoint['provider']} HTTP {response.status_code}: {response.text[:200]}"
                        )
                    payload = response.json()
                    content = payload["choices"][0]["message"]["content"]
                    data = _json.loads(content)
                    return schema.model_validate(data)
                except ValidationError as exc:
                    last_error = exc
                    if repair is None:
                        # Retry ONCE with the validation error, the invalid
                        # output and the schema reminder.
                        repair = build_repair_message(exc, data, schema)
                        logger.info("LLM schema mismatch; sending repair prompt: %s", exc)
                        continue
                    break  # repair failed too: next endpoint
                except Exception as exc:  # noqa: BLE001 - one bad endpoint must not kill the chain
                    # Network blips (DNS/timeouts), HTTP errors or invalid JSON
                    # fall through to the next configured endpoint.
                    logger.info("LLM endpoint %s failed: %r", endpoint["provider"], exc)
                    last_error = exc
                    break
        raise LLMError(f"All LLM endpoints failed: {last_error!r}")


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
    endpoints = settings.llm_endpoints
    if not endpoints:
        return NullLLMProvider()
    return OpenAICompatibleLLMProvider(endpoints)


async def generate_with_repair(
    provider: LLMProvider,
    raw_generator: Any,  # callable returning raw JSON-like dict; async or sync
    # (async callables may optionally accept one str argument: the repair message)
    schema: type[T],
    *,
    max_attempts: int = 2,
) -> T:
    last_error: Exception | None = None
    repair: str | None = None
    for _attempt in range(max_attempts):
        raw: Any = None
        try:
            raw = raw_generator(repair) if repair is not None else raw_generator()
            # Await only awaitables: a sync generator returns its dict directly
            # (awaiting it raised "'dict' object can't be awaited").
            if inspect.isawaitable(raw):
                raw = await raw
            return schema.model_validate(raw)
        except (ValidationError, LLMError, ValueError) as exc:
            last_error = exc
            # Repair message contains: validation error, invalid output, schema.
            repair = build_repair_message(exc, raw, schema)
    raise LLMError(f"LLM output failed validation after {max_attempts} attempts: {last_error}")
