"""User lookup and insert queries.

Backs the ``app.api.v1.auth`` router; the router keeps password/token logic,
email validation and the unique-race 409 handling.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User

# Transaction control for the auth router (register's flush/commit/rollback).
from app.db.repositories.base import commit as commit
from app.db.repositories.base import rollback as rollback


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(User.email == email))
    user: User | None = result.scalar_one_or_none()
    return user


async def add_user(session: AsyncSession, user: User) -> None:
    session.add(user)
    await session.flush()


async def get_user(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await session.get(User, user_id)
