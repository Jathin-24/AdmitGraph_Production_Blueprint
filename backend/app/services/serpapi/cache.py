"""Async search-result cache: Redis with a silent in-process fallback.

serpapi_docs integration rule 9 ("Cache identical searches") + rule 15. Redis
is tried first (JSON strings, TTL = settings.serpapi_cache_ttl_seconds). If
Redis is unreachable or errors, the adapter permanently falls back to an
in-process dict for the rest of the process lifetime — a cache outage must
never fail a research run.

The adapter stores *payload JSON only* (the raw provider response dict).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

_REDIS_TIMEOUT_SECONDS = 1.5


class SearchCache:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._memory: dict[str, tuple[float, str]] = {}  # key -> (expires_at, json)
        self._redis_enabled = True  # flipped off permanently on first failure
        self._redis_checked = False

    # -- helpers -----------------------------------------------------------
    def _ttl(self) -> int:
        return max(1, int(self._settings.serpapi_cache_ttl_seconds))

    async def _redis(self) -> Any | None:
        """Return a live Redis client, or None when Redis is unavailable."""
        if not self._redis_enabled:
            return None
        try:
            from app.core.redis import get_redis

            client = get_redis()
            if not self._redis_checked:
                # Probe eagerly with a bounded timeout so an unreachable server
                # degrades to the memory fallback instead of stalling searches.
                await asyncio.wait_for(client.ping(), timeout=_REDIS_TIMEOUT_SECONDS)
                self._redis_checked = True
            return client
        except Exception as exc:  # noqa: BLE001 - cache must never break a run
            if not self._redis_checked:
                logger.info("Redis cache unavailable, using in-process cache: %r", exc)
            self._redis_enabled = False
            return None

    def _memory_get(self, key: str) -> dict[str, Any] | None:
        entry = self._memory.get(key)
        if entry is None:
            return None
        expires_at, raw = entry
        if time.monotonic() >= expires_at:
            self._memory.pop(key, None)
            return None
        try:
            value = json.loads(raw)
        except ValueError:
            self._memory.pop(key, None)
            return None
        return value if isinstance(value, dict) else None

    # -- public API --------------------------------------------------------
    async def get(self, key: str) -> dict[str, Any] | None:
        client = await self._redis()
        if client is not None:
            try:
                raw = await asyncio.wait_for(client.get(key), timeout=_REDIS_TIMEOUT_SECONDS)
            except Exception as exc:  # noqa: BLE001
                logger.info("Redis get failed, falling back to memory cache: %r", exc)
                self._redis_enabled = False
            else:
                if raw:
                    try:
                        value = json.loads(raw)
                    except ValueError:
                        value = None
                    if isinstance(value, dict):
                        return value
        return self._memory_get(key)

    async def set(self, key: str, payload: dict[str, Any]) -> None:
        try:
            raw = json.dumps(payload)
        except (TypeError, ValueError):
            return  # non-serializable payloads are simply not cached
        ttl = self._ttl()
        self._memory[key] = (time.monotonic() + ttl, raw)
        client = await self._redis()
        if client is not None:
            try:
                await asyncio.wait_for(client.set(key, raw, ex=ttl), timeout=_REDIS_TIMEOUT_SECONDS)
            except Exception as exc:  # noqa: BLE001
                logger.info("Redis set failed, keeping memory cache only: %r", exc)
                self._redis_enabled = False

    @property
    def backend(self) -> str:
        """'redis' once a Redis round-trip has succeeded, else 'memory'."""
        return "redis" if self._redis_checked else "memory"
