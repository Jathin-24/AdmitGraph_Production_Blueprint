"""Authentication endpoints: register, login, current user.

Contract (API_CONTRACT / spec):
    POST /auth/register  {email, password, full_name?} -> 201 {token, user}
    POST /auth/login     {email, password}             -> 200 {token, user}
    GET  /auth/me        Bearer required               -> 200 {user}

Security notes (BACKEND_SPEC §Security):
- Passwords stored as Argon2id hashes only (app/core/security.py).
- Login failures never reveal whether the email exists (same 401 message,
  constant-work decoy verify for unknown accounts).
- Tokens are HS256 JWTs minted by create_token; the middleware decodes them
  into ContextVars, so /auth/me just reads current_user_id().
"""

from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.events import emit
from app.core.security import (
    create_token,
    current_user_id,
    hash_password,
    reset_request_user,
    set_request_user,
    verify_password,
)
from app.db.models import User, UserRole
from app.db.repositories import auth as auth_repo
from app.db.session import get_session
from app.services.profile import get_or_create_profile

router = APIRouter(tags=["auth"])

MIN_PASSWORD_LENGTH = 8
INVALID_CREDENTIALS_MESSAGE = "Incorrect email or password"
EMAIL_TAKEN_MESSAGE = "An account with this email already exists"


class RegisterInput(BaseModel):
    """All fields optional at the type level so validation failures surface as
    our 400 VALIDATION_ERROR envelope (helpful messages) instead of a 422."""

    model_config = ConfigDict(extra="ignore")

    email: str | None = None
    password: str | None = None
    full_name: str | None = None


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    email: str | None = None
    password: str | None = None


class AuthUserOut(BaseModel):
    id: str
    email: str
    full_name: str | None = None
    role: str


class AuthResponse(BaseModel):
    token: str
    user: AuthUserOut


class MeResponse(BaseModel):
    user: AuthUserOut


def normalize_email(raw: str) -> str:
    """Canonical email form: trimmed + lowercased (matches the unique index)."""
    return raw.strip().lower()


def _validate_email(raw: str | None) -> str:
    if raw is None or not raw.strip():
        raise AppError(400, "VALIDATION_ERROR", "Email is required")
    email = normalize_email(raw)
    local, sep, domain = email.partition("@")
    if not sep or not local or not domain or "." not in domain or " " in email:
        raise AppError(400, "VALIDATION_ERROR", "Enter a valid email address")
    return email


def _validate_password(raw: str | None) -> str:
    if raw is None or not raw:
        raise AppError(400, "VALIDATION_ERROR", "Password is required")
    if len(raw) < MIN_PASSWORD_LENGTH:
        raise AppError(
            400,
            "VALIDATION_ERROR",
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters long",
        )
    return raw


@lru_cache(maxsize=1)
def _decoy_hash() -> str:
    """Argon2id hash used to keep unknown-email logins at constant work."""
    return hash_password("decoy-not-a-real-account-password")


def _role_for_email(email: str) -> UserRole:
    admin_emails = {
        normalize_email(e) for e in get_settings().admin_emails.split(",") if e.strip()
    }
    return UserRole.ADMIN if email in admin_emails else UserRole.STUDENT


def _user_payload(user: User) -> AuthUserOut:
    return AuthUserOut(
        id=str(user.id),
        email=user.email,
        full_name=user.full_name,
        role=str(user.role),
    )


@router.post(
    "/auth/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(payload: RegisterInput, session: AsyncSession = Depends(get_session)) -> AuthResponse:
    email = _validate_email(payload.email)
    password = _validate_password(payload.password)
    full_name = payload.full_name.strip() if payload.full_name and payload.full_name.strip() else None

    existing = await auth_repo.get_user_by_email(session, email)
    if existing is not None:
        raise AppError(409, "EMAIL_TAKEN", EMAIL_TAKEN_MESSAGE)

    role = _role_for_email(email)
    user = User(email=email, full_name=full_name, password_hash=hash_password(password), role=role)
    try:
        await auth_repo.add_user(session, user)
        # Eagerly create the profile so GET /me/profile works immediately
        # after registration (ContextVar routes the service to this user).
        tokens = set_request_user(user.id, str(role))
        try:
            await get_or_create_profile(session)
            await auth_repo.commit(session)
        finally:
            reset_request_user(tokens)
    except IntegrityError:
        # Unique-index race with a concurrent register for the same email.
        await auth_repo.rollback(session)
        raise AppError(409, "EMAIL_TAKEN", EMAIL_TAKEN_MESSAGE) from None

    emit("user.registered", user_id=user.id, email=user.email, full_name=user.full_name)
    return AuthResponse(token=create_token(user.id, str(role)), user=_user_payload(user))


@router.post("/auth/login", response_model=AuthResponse)
async def login(payload: LoginInput, session: AsyncSession = Depends(get_session)) -> AuthResponse:
    email = _validate_email(payload.email)
    password = _validate_password(payload.password)

    user = await auth_repo.get_user_by_email(session, email)
    stored_hash = user.password_hash if user is not None else None
    # Unknown email still runs one Argon2 verify (decoy) so response time does
    # not reveal which accounts exist.
    ok = verify_password(stored_hash if stored_hash is not None else _decoy_hash(), password)
    if user is None or stored_hash is None or not ok:
        raise AppError(401, "INVALID_CREDENTIALS", INVALID_CREDENTIALS_MESSAGE)

    role = str(user.role)
    return AuthResponse(token=create_token(user.id, role), user=_user_payload(user))


@router.get("/auth/me", response_model=MeResponse)
async def me(session: AsyncSession = Depends(get_session)) -> MeResponse:
    uid = current_user_id()  # set by middleware only when a Bearer token was sent
    if uid is None:
        raise AppError(401, "UNAUTHENTICATED", "Authentication required")
    user = await auth_repo.get_user(session, uid)
    if user is None:
        # Token minted for a since-deleted account.
        raise AppError(401, "UNAUTHENTICATED", "Authentication required")
    return MeResponse(user=_user_payload(user))
