"""Single-use, hashed, expiring tokens for password reset / email verification.

Security properties (audit P2-14):

* the raw token is generated with :func:`secrets.token_urlsafe` and NEVER
  persisted — only its SHA-256 hash reaches the database, so a database dump
  yields no usable link;
* a token is bound to one user + one purpose (`PASSWORD_RESET`,
  `EMAIL_VERIFY`), expires after TOKEN_TTL_MINUTES and is spendable once
  (`used_at` is stamped on consumption);
* issuing a new token for (user, purpose) immediately invalidates that user's
  previous UNUSED tokens for the same purpose (stamping `used_at`), so only
  the newest emailed link works;
* validation is constant-shaped: hash match, not used, not expired — the API
  maps every failure to the same 400 so a prober cannot tell an expired
  token from an unknown one.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuthToken, AuthTokenPurpose
from app.db.repositories.auth import expire_unused_tokens, token_by_hash

TOKEN_TTL_MINUTES = 30
RAW_TOKEN_BYTES = 32


def generate_raw_token() -> str:
    """URL-safe random secret (256 bits) handed to the user's email link."""
    return secrets.token_urlsafe(RAW_TOKEN_BYTES)


def hash_token(raw: str) -> str:
    """SHA-256 hex digest of the raw token — the only form stored."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def issue_token(
    session: AsyncSession,
    user_id: uuid.UUID,
    purpose: AuthTokenPurpose,
    *,
    ttl_minutes: int = TOKEN_TTL_MINUTES,
) -> str:
    """Issue a fresh token for ``user_id``; returns the RAW token.

    Previous unused tokens for the same (user, purpose) are invalidated first.
    The caller is responsible for committing (usually together with the email
    send outcome, but the row must land even if delivery fails).
    """
    now = datetime.now(UTC)
    await expire_unused_tokens(session, user_id, purpose, now)
    raw = generate_raw_token()
    session.add(
        AuthToken(
            user_id=user_id,
            purpose=purpose.value,
            token_hash=hash_token(raw),
            expires_at=now + timedelta(minutes=ttl_minutes),
        )
    )
    await session.flush()
    return raw


async def find_valid_token(
    session: AsyncSession, raw: str, purpose: AuthTokenPurpose
) -> AuthToken | None:
    """The spendable row for ``raw``, or None (unknown / used / expired)."""
    if not raw:
        return None
    row = await token_by_hash(session, hash_token(raw), purpose)
    if row is None or row.used_at is not None:
        return None
    expires = row.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    if expires <= datetime.now(UTC):
        return None
    return row
