"""User lookup, insert and auth-token queries.

Backs the ``app.api.v1.auth`` router and ``app.services.auth.tokens``; the
router keeps password/token logic, email validation and the unique-race 409
handling, and the token service keeps hashing/expiry rules — the raw SQL
statements live here.
"""

import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuthToken, AuthTokenPurpose, User

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


async def expire_unused_tokens(
    session: AsyncSession, user_id: uuid.UUID, purpose: AuthTokenPurpose, now: datetime
) -> None:
    """Stamp every still-unused token for (user, purpose) as spent."""
    await session.execute(
        update(AuthToken)
        .where(
            AuthToken.user_id == user_id,
            AuthToken.purpose == purpose.value,
            AuthToken.used_at.is_(None),
        )
        .values(used_at=now)
    )


async def token_by_hash(
    session: AsyncSession, token_hash: str, purpose: AuthTokenPurpose
) -> AuthToken | None:
    """The stored token row for one hash + purpose (usage/expiry checks stay
    in the token service)."""
    row = (
        await session.execute(
            select(AuthToken).where(
                AuthToken.token_hash == token_hash,
                AuthToken.purpose == purpose.value,
            )
        )
    ).scalar_one_or_none()
    return row
