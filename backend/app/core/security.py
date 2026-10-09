"""Auth primitives: Argon2 password hashing, JWT bearer tokens, request-user context.

Required by backend/BACKEND_SPEC.md §Structure (app/core/security.py).

- Passwords: Argon2id via argon2-cffi (BACKEND_SPEC §Security: Argon2/bcrypt only).
- Tokens: HS256 JWT carrying sub (user id), role, iat, exp. The signing key is
  `settings.jwt_secret` when configured; production refuses to boot without it
  or when it is the committed `.env.example` placeholder / shorter than 32
  chars (audit D-3 — dev only warns), and every other environment gets a
  cryptographically random key generated at
  process start (persisted to the gitignored `backend/var/jwt_secret`, mode
  0600, so dev sessions survive restarts). The key is NEVER derived from
  DATABASE_URL or any other committed value — the old derivation
  `hmac(b"admitgraph-dev", DATABASE_URL)` was recomputable by anyone with the
  repo, which let them forge `{sub, role: "ADMIN"}` tokens.
- Request context: middleware decodes the bearer token once per request and
  stores it in ContextVars, so `get_or_create_profile` resolves the calling
  user without threading a parameter through every router (services stay
  callable directly in tests — ContextVars default to None → demo user).
- Staleness: `token_is_stale` tells the auth middleware when a decoded token
  must be rejected because `users.password_changed_at` moved past the token's
  issue time (handoff from W3: POST /auth/reset stamps the column, the
  middleware answers 401 TOKEN_STALE) — see app/main.py.
"""

from __future__ import annotations

import contextvars
import logging
import os
import secrets
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import get_settings
from app.core.errors import AppError

JWT_ALGORITHM = "HS256"

# --- JWT secret strength guard (audit D-3) -------------------------------
# `.env.example` ships `JWT_SECRET=change-me-long-random-string` so a copied
# example config cannot pass for a real deployment: production refuses that
# placeholder (and anything shorter than MIN_JWT_SECRET_LENGTH) outright;
# every other environment only warns — a dev boot must not be blocked by an
# example value.
EXAMPLE_JWT_SECRET_PLACEHOLDER = "change-me-long-random-string"
MIN_JWT_SECRET_LENGTH = 32
_weak_secret_warned = False

log = logging.getLogger(__name__)

_hasher = PasswordHasher()

# Development-only signing-key persistence. `backend/var/` is gitignored, so
# the generated secret never lands in the repo or an image built from it; the
# file keeps local sessions alive across restarts without making the key
# derivable from any public/committed value.
_DEV_SECRET_FILE = Path(__file__).resolve().parents[2] / "var" / "jwt_secret"

# Process-start fallback key (only used when JWT_SECRET is unset outside
# production). Generated once per process; see jwt_secret().
_generated_secret: str | None = None

_current_user_id: contextvars.ContextVar[uuid.UUID | None] = contextvars.ContextVar(
    "current_user_id", default=None
)
_current_user_role: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_user_role", default=None
)


def _load_or_create_dev_secret() -> tuple[str, bool]:
    """Create (or re-read) the gitignored dev signing key file.

    Returns ``(secret, restart_stable)``. The secret is always a fresh
    ``secrets.token_urlsafe(64)`` — never computed from configuration — so it
    cannot be reproduced from committed values.
    """
    fresh = secrets.token_urlsafe(64)
    try:
        if _DEV_SECRET_FILE.is_file():
            existing = _DEV_SECRET_FILE.read_text(encoding="utf-8").strip()
            if existing:
                return existing, True
        _DEV_SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(_DEV_SECRET_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(fresh)
        return fresh, True
    except FileExistsError:
        # Lost a creation race: adopt the winner's key so processes agree.
        try:
            existing = _DEV_SECRET_FILE.read_text(encoding="utf-8").strip()
        except OSError:
            existing = ""
        return (existing, True) if existing else (fresh, False)
    except OSError:
        # Read-only filesystem (e.g. hardened container): fall back to a
        # per-process key — sessions then die on restart (logged loudly below).
        return fresh, False


def _weak_jwt_secret(secret: str) -> bool:
    """True for the committed example placeholder or an under-length secret."""
    return secret.startswith(EXAMPLE_JWT_SECRET_PLACEHOLDER) or len(secret) < MIN_JWT_SECRET_LENGTH


def jwt_secret() -> str:
    """HS256 signing key.

    Precedence:

    1. ``settings.jwt_secret`` whenever explicitly configured — production
       additionally REQUIRES it to be real (audit D-3): the committed
       ``.env.example`` placeholder or a secret shorter than
       ``MIN_JWT_SECRET_LENGTH`` raises ``RuntimeError`` instead of silently
       signing tokens. Other environments accept it but warn once per process.
    2. ``APP_ENV=production`` with no secret → ``RuntimeError`` (fail closed:
       refuse to start rather than sign tokens with a guessable key).
    3. Otherwise a cryptographically random key generated once per process and
       persisted to the gitignored ``backend/var/jwt_secret`` (mode 0600) when
       the directory is writable, so development sessions survive restarts.
       The key is never derived from DATABASE_URL or any other config value.
    """
    global _weak_secret_warned
    settings = get_settings()
    if settings.jwt_secret:
        if settings.app_env == "production":
            if settings.jwt_secret.startswith(EXAMPLE_JWT_SECRET_PLACEHOLDER):
                raise RuntimeError(
                    "JWT_SECRET is the .env.example placeholder "
                    f"({EXAMPLE_JWT_SECRET_PLACEHOLDER!r}) and APP_ENV=production refuses it; "
                    "generate a real secret with: "
                    'python -c "import secrets; print(secrets.token_urlsafe(64))"'
                )
            if len(settings.jwt_secret) < MIN_JWT_SECRET_LENGTH:
                raise RuntimeError(
                    f"JWT_SECRET must be at least {MIN_JWT_SECRET_LENGTH} characters when "
                    "APP_ENV=production; generate one with: "
                    'python -c "import secrets; print(secrets.token_urlsafe(64))"'
                )
        elif _weak_jwt_secret(settings.jwt_secret) and not _weak_secret_warned:
            # Warn, never fail: dev/test boots keep working (audit D-3).
            log.warning(
                "JWT_SECRET is weak (the .env.example placeholder or shorter than %d chars): "
                "fine for development only — APP_ENV=production refuses this value. "
                "Generate a real secret with: "
                'python -c "import secrets; print(secrets.token_urlsafe(64))"',
                MIN_JWT_SECRET_LENGTH,
            )
            _weak_secret_warned = True
        return settings.jwt_secret
    if settings.app_env == "production":
        raise RuntimeError(
            "JWT_SECRET must be set when APP_ENV=production; "
            "refusing to start with an ephemeral signing key"
        )
    global _generated_secret
    if _generated_secret is None:
        _generated_secret, restart_stable = _load_or_create_dev_secret()
        if restart_stable:
            log.warning(
                "JWT_SECRET is not configured: using a generated development signing key "
                "persisted to %s (gitignored). Set JWT_SECRET explicitly for production.",
                _DEV_SECRET_FILE,
            )
        else:
            log.warning(
                "JWT_SECRET is not configured and %s is not writable: using a per-process "
                "random signing key — ALL SESSIONS WILL BE LOGGED OUT ON RESTART. "
                "Set JWT_SECRET to a stable secret.",
                _DEV_SECRET_FILE,
            )
    return _generated_secret


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def _epoch_seconds(value: datetime) -> int:
    """Epoch seconds for a timestamp; naive values are read as UTC.

    The column is timezone-aware (alembic: DateTime(timezone=True)), but
    tests/mocks may hand us a naive datetime — interpreting it as UTC keeps
    the comparison stable instead of shifting by the host's offset.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp())


def create_token(
    user_id: uuid.UUID,
    role: str,
    ttl_seconds: int | None = None,
    password_changed_at: datetime | None = None,
) -> str:
    """Sign an HS256 access token carrying sub, role, iat, exp.

    ``password_changed_at`` is an OPTIONAL kwarg so every existing caller
    (app/api/v1/auth.py, tests) compiles unchanged. When passed, the token
    also carries a ``pwd`` claim (epoch seconds of users.password_changed_at)
    so :func:`token_is_stale` can detect a later password change. Callers
    that do not pass it are covered by the ``iat`` branch of
    :func:`token_is_stale` instead — every token ever minted here has ``iat``,
    so a token issued BEFORE the last password change is rejected regardless
    of whether the ``pwd`` claim exists.
    """
    settings = get_settings()
    ttl = ttl_seconds if ttl_seconds is not None else settings.jwt_ttl_seconds
    now = int(__import__("time").time())
    payload: dict[str, Any] = {"sub": str(user_id), "role": role, "iat": now, "exp": now + ttl}
    if password_changed_at is not None:
        payload["pwd"] = _epoch_seconds(password_changed_at)
    return jwt.encode(payload, jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, Any] | None:
    """Return claims for a valid, unexpired token; None otherwise (never raises).

    Besides the historical ``sub``/``role`` keys, ``iat`` (and ``pwd`` when
    the token carries one) is forwarded so the auth middleware can run
    :func:`token_is_stale`.
    """
    try:
        claims = jwt.decode(token, jwt_secret(), algorithms=[JWT_ALGORITHM])
    except jwt.InvalidTokenError:
        return None
    sub = claims.get("sub")
    if not isinstance(sub, str):
        return None
    result: dict[str, Any] = {"sub": sub, "role": str(claims.get("role", "STUDENT"))}
    if "iat" in claims:
        result["iat"] = claims["iat"]
    if "pwd" in claims:
        result["pwd"] = claims["pwd"]
    return result


def token_is_stale(claims: Mapping[str, Any], password_changed_at: datetime | None) -> bool:
    """True when a password change invalidated this token (W3 handoff).

    Checked in app/main.py's auth middleware after a successful decode:

    * ``password_changed_at is None`` → ``False`` (password never changed);
    * a ``pwd`` claim (present only when create_token was given the
      timestamp) → stale when it differs from the CURRENT
      ``password_changed_at`` — the password changed again after minting;
    * otherwise the ``iat`` claim (present on every token ever minted here,
      so tokens issued before this helper existed are covered too, not
      grandfathered) → stale when issued BEFORE the last password change.

    Both comparisons are second-granularity with the timestamp floored, so a
    token minted in the same second as the change is not rejected (an
    immediate re-login must work); anything strictly older is. Claims that
    cannot be interpreted fail CLOSED (stale): a token we cannot date is a
    token we cannot trust.
    """
    if password_changed_at is None:
        return False
    changed_epoch = _epoch_seconds(password_changed_at)
    if "pwd" in claims:
        pwd = claims["pwd"]
        if not isinstance(pwd, (int, float)):
            return True
        # Direct comparison (no int() cast): NaN compares unequal → stale,
        # and no numeric edge can raise inside the middleware.
        return pwd != changed_epoch
    iat = claims.get("iat")
    if not isinstance(iat, (int, float)):
        return True
    try:
        return int(iat) < changed_epoch
    except (ValueError, OverflowError):  # NaN/inf cannot be dated → fail closed
        return True


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
