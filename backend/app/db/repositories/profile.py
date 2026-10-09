"""Profile router and service data access.

Row handling for ``/me/profile`` lives in ``app.services.profile``; the
router's only session operation is the transaction commit that persists the
service's writes, re-exported here so every ``session`` call stays in the
repository layer. The service's profile lookup lives here too (its user
lookup is the shared auth-repo query), so no SQL statement appears under
``app.services.profile``.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import StudentProfile

# Shared user-by-email query (same statement the auth router uses).
from app.db.repositories.auth import get_user_by_email as get_user_by_email

# Transaction control for the profile router.
from app.db.repositories.base import commit as commit


async def get_profile_by_user(session: AsyncSession, user_id: uuid.UUID) -> StudentProfile | None:
    result = await session.execute(select(StudentProfile).where(StudentProfile.user_id == user_id))
    return result.scalar_one_or_none()
