"""Auth primitives: Argon2 password hashing, JWT bearer tokens, request-user context.

Required by backend/BACKEND_SPEC.md §Structure (app/core/security.py).

- Passwords: Argon2id via argon2-cffi (BACKEND_SPEC §Security: Argon2/bcrypt only).
- Tokens: HS256 JWT carrying sub (user id), role, iat, exp.
- Request context: middleware decodes the bearer token once per request and
  stores it in ContextVars, so `get_or_create_profile` resolves the calling
  user without threading a parameter through every router (services stay
  callable directly in tests — ContextVars default to None → demo user).
"""

from __future__ import annotations

import contextvars
import hmac
import uuid

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import get_settings
from app.core.errors import AppError

JWT_ALGORITHM = "HS256"

_hasher = PasswordHasher()

_current_user_id: contextvars.ContextVar[uuid.UUID | None] = contextvars.ContextVar(
    "current_user_id", default=None
)
_current_user_role: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_user_role", default=None
)


def jwt_secret() -> str:
    """Secret for token signing. Production requires an explicit secret."""
    secret = get_settings().jwt_secret
    if secret:
        return secret
    if get_settings().app_env == "production":
        raise RuntimeError("JWT_SECRET must be set when APP_ENV=production")
    # Development fallback: stable per-database secret so restarts keep sessions.
    return hmac.new(
        b"admitgraph-dev",
        get_settings().database_url.encode(),
        "sha256",
    ).hexdigest()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def create_token(user_id: uuid.UUID, role: str, ttl_seconds: int | None = None) -> str:
    settings = get_settings()
    ttl = ttl_seconds if ttl_seconds is not None else settings.jwt_ttl_seconds
    now = int(__import__("time").time())
    payload = {"sub": str(user_id), "role": role, "iat": now, "exp": now + ttl}
    return jwt.encode(payload, jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, str] | None:
    """Return claims for a valid, unexpired token; None otherwise (never raises)."""
    try:
        claims = jwt.decode(token, jwt_secret(), algorithms=[JWT_ALGORITHM])
    except jwt.InvalidTokenError:
        return None
    sub = claims.get("sub")
    if not isinstance(sub, str):
        return None
    return {"sub": sub, "role": str(claims.get("role", "STUDENT"))}


def set_request_user(
    user_id: uuid.UUID, role: str
) -> tuple[contextvars.Token[uuid.UUID | None], contextvars.Token[str | None]]:
    return _current_user_id.set(user_id), _current_user_role.set(role)


def reset_request_user(
    tokens: tuple[contextvars.Token[uuid.UUID | None], contextvars.Token[str | None]],
) -> None:
    _current_user_id.reset(tokens[0])
    _current_user_role.reset(tokens[1])


def current_user_id() -> uuid.UUID | None:
    return _current_user_id.get()


def current_user_role() -> str | None:
    return _current_user_role.get()


def bearer_token(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        return token or None
    return None


def require_admin() -> None:
    """FastAPI dependency fragment: raise 403 unless the request user is ADMIN."""
    if current_user_role() != "ADMIN":
        raise AppError(403, "FORBIDDEN", "Administrator access required")
