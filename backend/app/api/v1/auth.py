"""Authentication endpoints: register, login, current user, password reset,
email verification.

Contract (API_CONTRACT / spec):
    POST /auth/register  {email, password, full_name?} -> 201 {token, user}
    POST /auth/login     {email, password}             -> 200 {token, user}
    GET  /auth/me        Bearer required               -> 200 {user}
    POST /auth/forgot    {email}                       -> 202 {status:"accepted"}
    POST /auth/reset     {token, password}             -> 200 {status:"reset"}
    POST /auth/verify-request {email}                  -> 202 {status:"accepted"}
    POST /auth/verify    {token}                       -> 200 {status:"verified"}

Security notes (BACKEND_SPEC §Security):
- Passwords stored as Argon2id hashes only (app/core/security.py).
- Login failures never reveal whether the email exists (same 401 message,
  constant-work decoy verify for unknown accounts).
- Tokens are HS256 JWTs minted by create_token; the middleware decodes them
  into ContextVars, so /auth/me just reads current_user_id().
- /auth/forgot and /auth/verify-request NEVER reveal whether the email has an
  account (always 202). Reset/verify links carry a raw random token whose
  SHA-256 hash is the only thing stored (app/services/auth/tokens.py).
- HANDOFF (security.py is owned by W1): invalidating still-valid JWTs after a
  password change is a security.py concern and is NOT done here.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
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
from app.db.models import AuthTokenPurpose, User, UserRole
from app.db.repositories import auth as auth_repo
from app.db.session import get_session
from app.services.auth.tokens import find_valid_token, issue_token
from app.services.mail import send_email
from app.services.mail.templates import email_verify_email, password_reset_email
from app.services.profile import get_or_create_profile

router = APIRouter(tags=["auth"])

MIN_PASSWORD_LENGTH = 8
INVALID_CREDENTIALS_MESSAGE = "Incorrect email or password"
EMAIL_TAKEN_MESSAGE = "An account with this email already exists"
INVALID_RESET_TOKEN_MESSAGE = "This reset link is invalid or has expired"
INVALID_VERIFY_TOKEN_MESSAGE = "This verification link is invalid or has expired"


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


# --- P2-14: password reset + email verification -------------------------------


class ForgotInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    email: str | None = None


class ResetInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    token: str | None = None
    password: str | None = None


class VerifyRequestInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    email: str | None = None


class VerifyInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    token: str | None = None


class StatusOut(BaseModel):
    status: str


def _frontend_link(path: str) -> str:
    """Absolute frontend URL for an emailed one-time link."""
    return f"{get_settings().frontend_url.rstrip('/')}{path}"


async def _send_one_time_link(
    session: AsyncSession,
    user: User,
    purpose: AuthTokenPurpose,
    path: str,
    template: Callable[[str, str], tuple[str, str, str]],
) -> None:
    """Issue + commit + email a single-use link. Never raises.

    The token row must land even if delivery fails, so the commit happens
    before the (send_email itself never raises either).
    """
    raw = await issue_token(session, user.id, purpose)
    await auth_repo.commit(session)
    link = _frontend_link(f"{path}?token={raw}")
    name = user.full_name.strip() if user.full_name and user.full_name.strip() else "there"
    subject, html, text = template(name, link)
    await send_email(user.email, subject, html, text)


@router.post(
    "/auth/forgot",
    response_model=StatusOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def forgot(
    payload: ForgotInput, session: AsyncSession = Depends(get_session)
) -> StatusOut:
    """Request a password-reset link.

    Always 202 {status:"accepted"} — the response never reveals whether the
    email has an account (unknown addresses simply produce no email).
    """
    email = _validate_email(payload.email)
    user = await auth_repo.get_user_by_email(session, email)
    # Accounts without a password hash (e.g. seeded demo) get no link, but the
    # response is identical so existence still does not leak.
    if user is not None and user.password_hash is not None:
        await _send_one_time_link(
            session,
            user,
            AuthTokenPurpose.PASSWORD_RESET,
            "/reset-password",
            password_reset_email,
        )
        emit("auth.password_reset_requested", user_id=user.id, email=user.email)
    return StatusOut(status="accepted")


@router.post("/auth/reset", response_model=StatusOut)
async def reset(
    payload: ResetInput, session: AsyncSession = Depends(get_session)
) -> StatusOut:
    """Consume a reset token and set a new password.

    Token is validated FIRST so an expired/unknown link always yields the
    same 400 INVALID_TOKEN (no "password" in the message — the frontend uses
    that to route between token-invalid and password-error UI). Password
    problems surface as 400 VALIDATION_ERROR whose message does contain
    "Password".
    """
    row = await find_valid_token(session, payload.token or "", AuthTokenPurpose.PASSWORD_RESET)
    if row is None:
        raise AppError(400, "INVALID_TOKEN", INVALID_RESET_TOKEN_MESSAGE)
    password = _validate_password(payload.password)
    user = await auth_repo.get_user(session, row.user_id)
    if user is None:
        raise AppError(400, "INVALID_TOKEN", INVALID_RESET_TOKEN_MESSAGE)
    now = datetime.now(UTC)
    user.password_hash = hash_password(password)
    user.password_changed_at = now
    row.used_at = now  # single-use
    await auth_repo.commit(session)
    emit("auth.password_reset", user_id=user.id, email=user.email)
    return StatusOut(status="reset")


@router.post(
    "/auth/verify-request",
    response_model=StatusOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def verify_request(
    payload: VerifyRequestInput, session: AsyncSession = Depends(get_session)
) -> StatusOut:
    """Request an email-verification link. Always 202 {status:"accepted"}."""
    email = _validate_email(payload.email)
    user = await auth_repo.get_user_by_email(session, email)
    if user is not None:
        await _send_one_time_link(
            session,
            user,
            AuthTokenPurpose.EMAIL_VERIFY,
            "/verify-email",
            email_verify_email,
        )
        emit("auth.email_verification_requested", user_id=user.id, email=user.email)
    return StatusOut(status="accepted")


@router.post("/auth/verify", response_model=StatusOut)
async def verify(
    payload: VerifyInput, session: AsyncSession = Depends(get_session)
) -> StatusOut:
    """Consume a verification token; stamps users.email_verified_at."""
    row = await find_valid_token(session, payload.token or "", AuthTokenPurpose.EMAIL_VERIFY)
    if row is None:
        raise AppError(400, "INVALID_TOKEN", INVALID_VERIFY_TOKEN_MESSAGE)
    user = await auth_repo.get_user(session, row.user_id)
    if user is None:
        raise AppError(400, "INVALID_TOKEN", INVALID_VERIFY_TOKEN_MESSAGE)
    now = datetime.now(UTC)
    if user.email_verified_at is None:
        user.email_verified_at = now
    row.used_at = now  # single-use
    await auth_repo.commit(session)
    emit("auth.email_verified", user_id=user.id, email=user.email)
    return StatusOut(status="verified")
