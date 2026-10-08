"""Shared transaction control for the repository layer.

Route handlers keep HTTP shaping, including mutations of already-loaded
models, but hand every ``session`` operation to the repository layer, so no
SQL-adjacent call ever appears under ``app.api.v1``.
"""

from sqlalchemy.ext.asyncio import AsyncSession


async def commit(session: AsyncSession) -> None:
    await session.commit()


async def rollback(session: AsyncSession) -> None:
    await session.rollback()


async def refresh(session: AsyncSession, instance: object) -> None:
    await session.refresh(instance)
