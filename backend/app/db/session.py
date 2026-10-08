from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _sync_url_to_async(url: str) -> str:
    # DATABASE_URL uses postgresql+psycopg; async engine works with the same driver.
    return url


def get_engine() -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(_sync_url_to_async(settings.database_url), pool_pre_ping=True)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def dispose_engine() -> None:
    """Drop the cached engine/sessionmaker so the next get_engine() re-reads settings.

    Used by integration tests that repoint DATABASE_URL at a scratch database,
    and by any code path that reloads configuration at runtime.
    """
    global _engine, _sessionmaker
    engine, _engine, _sessionmaker = _engine, None, None
    if engine is not None:
        import asyncio

        async def _quiet_dispose(target: AsyncEngine) -> None:
            # Best-effort: connections created on an already-closed event
            # loop raise on close; never surface that as an unhandled task
            # exception (the pool is abandoned either way).
            try:
                await target.dispose()
            except Exception:  # noqa: BLE001
                pass

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            asyncio.run(_quiet_dispose(engine))
        else:
            loop.create_task(_quiet_dispose(engine))


async def get_session() -> AsyncIterator[AsyncSession]:
    if _sessionmaker is None:
        get_engine()
    assert _sessionmaker is not None
    async with _sessionmaker() as session:
        yield session
