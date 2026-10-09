import asyncio
import json
import logging
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.requests import ClientDisconnect
from starlette.types import Message, Receive

from app.api.v1 import (
    admin,
    applications,
    auth,
    documents,
    evidence,
    health,
    monitor,
    notifications,
    onboarding,
    profile,
    programs,
    research,
    scholarships,
    strategies,
)
from app.core import metrics
from app.core.config import get_settings
from app.core.errors import (
    AppError,
    app_error_handler,
    error_payload,
    unhandled_error_handler,
    validation_error_handler,
)
from app.core.logging import configure_logging
from app.core.runcontext import request_id_scope
from app.core.security import (
    bearer_token,
    current_user_id,
    decode_token,
    jwt_secret,
    reset_request_user,
    set_request_user,
    token_is_stale,
)
from app.db.session import dispose_engine

configure_logging()
settings = get_settings()
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):  # type: ignore[no-untyped-def]
    """Recover interrupted runs, then start/stop background workers.

    The recovery pass (MASTER_SPEC §19 "Idempotent research runs") is
    best-effort: a database that is down at boot must not block startup.
    """
    from app.workers import recovery, scheduler

    # Fail fast (P0-1): with APP_ENV=production and no JWT_SECRET this raises
    # and aborts boot instead of running with an ephemeral/derivable key.
    # Outside production it also materialises (and persists) the dev key once
    # per process, so the first request does not pay for it.
    jwt_secret()

    try:
        await recovery.recover_research_plans()
    except Exception:  # noqa: BLE001 - startup must survive an unreachable database
        log.exception("research startup recovery failed")
    await scheduler.start()
    try:
        yield
    finally:
        await scheduler.stop()
        # Drop pooled connections created during this process: asyncpg
        # connections are bound to the loop that opened them, and shutting
        # the engine down here keeps them from leaking into the next app.
        dispose_engine()


app = FastAPI(
    title="AdmitGraph API",
    version="0.1.0",
    openapi_url="/api/v1/openapi.json",
    docs_url="/api/v1/docs",
    lifespan=lifespan,
)

app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
app.add_exception_handler(Exception, unhandled_error_handler)


MAX_BODY_BYTES = 11_000_000
# Hard cap slightly above the documented <=10 MB file-upload contract
# (PLAN cross-WS: POST /api/v1/documents/{id}/upload, multipart + envelope),
# and still a bound on JSON bodies. Enforced on Content-Length AND while the
# body streams — see _capped_receive below.
RATE_LIMIT_WINDOW_SECONDS = 60
# Memory bound for the in-process rate-limit map: when this many keys are
# tracked, expired windows are swept before recording a hit, and live keys
# closest to expiry are evicted if too many remain (see _sweep_rate_counters).
RATE_LIMIT_MAX_KEYS = 10_000

# Expensive endpoints get the per-user/per-IP budget (TEST_PLAN §Security:
# "rate limit expensive endpoints"). Static paths are matched exactly; the
# dynamic provider-spend POSTs (evidence recheck, monitor check) are matched
# by pattern below, so every call counts against the same budget.
_RATE_LIMITED_PATHS = frozenset(
    {
        "/api/v1/research/runs",
        "/api/v1/research/plan",
        "/api/v1/research/demo",
        "/api/v1/monitor/subscriptions",
        "/api/v1/auth/login",
        "/api/v1/auth/register",
    }
)

# GET endpoints that spend provider (SerpApi/LLM) budget. Verified when the
# limiter was made method-aware: every provider call sits behind a POST
# (research runs/plan/demo, monitor subscription check, evidence recheck), so
# this set is empty today — list any future provider-spend GET path here to
# give it the same budget as the POSTs above.
_RATE_LIMITED_GET_PATHS: frozenset[str] = frozenset()

# P2-14 close-out (handoff from W3): POST /auth/forgot and /auth/verify-request
# send an email per call, so an unthrottled burst is an email-bomb vector.
# They get a dedicated budget instead of the general per-user one below: two
# fixed-window buckets per request — one per caller IP (one host cannot spray
# many mailboxes) and one per target email (a botnet cannot hammer one
# mailbox). Either bucket over budget -> 429.
_RESET_REQUEST_PATHS = frozenset({"/api/v1/auth/forgot", "/api/v1/auth/verify-request"})
RESET_REQUEST_LIMIT_PER_MINUTE = 5

_BODY_CARRYING_METHODS = frozenset({"POST", "PATCH", "PUT"})


def _is_rate_limited(request: Request) -> bool:
    path = request.url.path
    if request.method == "POST":
        if path in _RATE_LIMITED_PATHS:
            return True
        # POST /api/v1/evidence/{evidence_id}/recheck and
        # POST /api/v1/monitor/subscriptions/{id}/check trigger provider work.
        if path.startswith("/api/v1/evidence/") and path.endswith("/recheck"):
            return True
        if path.startswith("/api/v1/monitor/subscriptions/") and path.endswith("/check"):
            return True
        return False
    if request.method == "GET":
        return path in _RATE_LIMITED_GET_PATHS
    return False


# Per-process fixed-window counters: bucket key -> (count, window_start).
# NOTE: this limiter is per-process — its state lives only in this process's
# memory, so a multi-process deployment (uvicorn workers, replicas) gives each
# process a separate budget. A globally enforced limit needs a shared store
# (e.g. Redis, already a dependency); deliberately not wired here to keep the
# limiter dependency-free, deterministic and testable.
_rate_counters: dict[str, tuple[int, float]] = {}


def _rate_limit_key(request: Request) -> str:
    """One bucket per authenticated user; anonymous callers share the IP bucket.

    The auth middleware runs BEFORE this one (see the middleware-order comment
    below), so a valid bearer token has already been decoded into ContextVars
    here; invalid tokens never reach the limiter (rejected with 401 upstream).
    """
    uid = current_user_id()
    if uid is not None:
        return f"user:{uid}"
    host = request.client.host if request.client else "unknown"
    return f"ip:{host}"


def _sweep_rate_counters(now: float) -> None:
    """Reclaim buckets so the in-process map stays bounded.

    Expired windows are evicted first; if too many LIVE keys remain (a flood
    of distinct users/IPs), the closest-to-expiry keys are evicted next so the
    map cannot grow past RATE_LIMIT_MAX_KEYS after the caller inserts.
    """
    expired = [
        key
        for key, (_count, started) in _rate_counters.items()
        if now - started > RATE_LIMIT_WINDOW_SECONDS
    ]
    for key in expired:
        del _rate_counters[key]
    excess = len(_rate_counters) - RATE_LIMIT_MAX_KEYS + 1
    if excess > 0:
        oldest_first = sorted(_rate_counters.items(), key=lambda item: item[1][1])
        for key, _value in oldest_first[:excess]:
            del _rate_counters[key]


def _record_rate_hit(key: str, limit: int) -> bool:
    """Record one fixed-window hit for ``key``; True when the budget is spent.

    Shared by the general per-user/IP budget and the dedicated reset-request
    budget so both behave identically (window rollover, memory-bound sweep).
    """
    now = time.time()
    if len(_rate_counters) >= RATE_LIMIT_MAX_KEYS:
        _sweep_rate_counters(now)
    count, window_start = _rate_counters.get(key, (0, now))
    if now - window_start > RATE_LIMIT_WINDOW_SECONDS:
        count, window_start = 0, now
    count += 1
    _rate_counters[key] = (count, window_start)
    return count > limit


def _reset_target_email(request: Request) -> str | None:
    """Target email from the JSON body already read by the body-cap step above.

    Best effort: an absent/unparsable body means no per-email bucket (the
    per-IP bucket still applies). The body-cap step has already cached the
    bytes on this request for every body-carrying request with framing
    headers, so this never re-reads the network.
    """
    body: bytes | None = getattr(request, "_body", None)
    if not body:
        return None
    try:
        data = json.loads(body)
    except ValueError:  # includes UnicodeDecodeError, a ValueError subclass
        return None
    if isinstance(data, dict):
        email = data.get("email")
        if isinstance(email, str) and email.strip():
            return email.strip().lower()
    return None


def _over_reset_request_limit(request: Request) -> bool:
    """True when this caller spent its forgot-password/verify-email budget.

    Both buckets are always recorded (no short-circuit on the first over-
    budget hit) so neither can be kept "free" by overspend on the other.
    """
    host = request.client.host if request.client else "unknown"
    over_ip = _record_rate_hit(f"reset-ip:{host}", RESET_REQUEST_LIMIT_PER_MINUTE)
    email = _reset_target_email(request)
    if email is None:
        return over_ip
    over_email = _record_rate_hit(f"reset-email:{email}", RESET_REQUEST_LIMIT_PER_MINUTE)
    return over_ip or over_email


def _rate_limited(request: Request) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content=error_payload(
            "RATE_LIMITED",
            "Too many requests",
            {},
            getattr(request.state, "request_id", ""),
        ),
    )


class _BodyTooLarge(Exception):
    """Raised by the counting receive wrapper when MAX_BODY_BYTES is exceeded.

    Private to this module and always caught in the middleware below — it must
    never cross a framework boundary (anyio task groups wrap escapees in an
    ExceptionGroup, which frameworks convert to an unrelated 400).
    """


def _payload_too_large(request: Request) -> JSONResponse:
    return JSONResponse(
        status_code=413,
        content=error_payload(
            "PAYLOAD_TOO_LARGE",
            "Request body too large",
            {},
            getattr(request.state, "request_id", ""),
        ),
    )


def _capped_receive(receive: Receive) -> Receive:
    """Wrap the ASGI receive channel to count body bytes as they are read.

    Content-Length is advisory only: chunked bodies omit it entirely and a
    client may send a smaller value than the body it actually streams, so the
    authoritative count happens here while the body is read. Aborts at the cap
    instead of buffering the whole oversized body.
    """
    total = 0

    async def counting_receive() -> Message:
        nonlocal total
        message = await receive()
        if message["type"] == "http.request":
            total += len(message.get("body", b""))
            if total > MAX_BODY_BYTES:
                raise _BodyTooLarge
        return message

    return counting_receive


@app.middleware("http")
async def body_size_and_rate_limit(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    if request.method in _BODY_CARRYING_METHODS:
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
            return _payload_too_large(request)
        # Enforce the cap while the body is actually read, not just from the
        # header: reading it here caches it for everything downstream
        # (Starlette's BaseHTTPMiddleware passes the cached body on), so
        # endpoints observe no difference; the counting wrapper aborts at the
        # cap before an oversized body can exhaust memory. A body-carrying
        # request that declares neither Content-Length nor Transfer-Encoding
        # has an empty body under HTTP/1.1 framing — skip the read so a
        # bodyless request can never block waiting for bytes.
        if length is not None or "transfer-encoding" in request.headers:
            request._receive = _capped_receive(request._receive)
            try:
                await request.body()
            except _BodyTooLarge:
                return _payload_too_large(request)
            except ClientDisconnect:
                return JSONResponse(
                    status_code=400,
                    content=error_payload(
                        "INVALID_REQUEST",
                        "Client disconnected before the body was received",
                        {},
                        getattr(request.state, "request_id", ""),
                    ),
                )
    if request.method == "POST" and request.url.path in _RESET_REQUEST_PATHS:
        over_budget = _over_reset_request_limit(request)
    elif _is_rate_limited(request):
        over_budget = _record_rate_hit(_rate_limit_key(request), settings.rate_limit_per_minute)
    else:
        over_budget = False
    if over_budget:
        return _rate_limited(request)
    return await call_next(request)


async def _password_changed_at(user_id: uuid.UUID) -> datetime | None:
    """Current ``users.password_changed_at`` for a token's subject (one PK SELECT).

    Returns None when the account no longer exists (endpoints answer their
    own 401 for deleted accounts — see /auth/me) or when the lookup itself
    fails: the staleness check fails OPEN so a database blip can never mass-
    invalidate every session at once; a request that truly needs the database
    dies on its own downstream access anyway.
    """
    try:
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from app.db.models import User  # local: keep module import light
        from app.db.session import get_engine

        maker = async_sessionmaker(get_engine(), expire_on_commit=False)
        async with maker() as session:
            row = await session.get(User, user_id)
            return row.password_changed_at if row is not None else None
    except Exception as exc:  # noqa: BLE001 - availability beats strictness here
        log.warning("password_changed_at check skipped for user %s: %s", user_id, exc)
        return None


@app.middleware("http")
async def auth_context_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Decode bearer token into request ContextVars (see app/core/security.py).

    No token → anonymous/demo user. Invalid/expired token → 401 so clients
    drop the stale session instead of silently writing to the wrong user.
    A token that predates the user's last password change → 401 TOKEN_STALE
    (W3 handoff: POST /auth/reset stamps users.password_changed_at; older
    sessions must not survive it).
    """
    token = bearer_token(request.headers.get("Authorization"))
    tokens: tuple[object, object] | None = None
    if token is not None:
        claims = decode_token(token)
        if claims is None:
            return JSONResponse(
                status_code=401,
                content=error_payload(
                    "UNAUTHENTICATED",
                    "Invalid or expired token",
                    {},
                    getattr(request.state, "request_id", ""),
                ),
            )
        user_id = uuid.UUID(claims["sub"])
        if token_is_stale(claims, await _password_changed_at(user_id)):
            return JSONResponse(
                status_code=401,
                content=error_payload(
                    "TOKEN_STALE",
                    "Password changed after this token was issued; sign in again",
                    {},
                    getattr(request.state, "request_id", ""),
                ),
            )
        tokens = set_request_user(user_id, claims["role"])
    try:
        return await call_next(request)
    finally:
        if tokens is not None:
            reset_request_user(tokens)  # type: ignore[arg-type]


@app.middleware("http")
async def request_id_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    # Bound before call_next so every downstream log record (auth, rate limit,
    # router, error handlers) carries request_id via app.core.runcontext.
    with request_id_scope(request_id):
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:
            # Render unhandled errors HERE (audit P2-27): left to
            # ServerErrorMiddleware (outermost), the 500 response would be
            # produced after this scope unwound — no X-Request-ID header, no
            # timing/security headers, and a traceback log with an empty
            # request_id. The handler logs the traceback under the live scope.
            response = await unhandled_error_handler(request, exc)
        duration_s = time.perf_counter() - start
    response.headers["X-Request-ID"] = request_id
    duration_ms = int(duration_s * 1000)
    # Observed on every path that reaches this middleware, including the 500s
    # rendered above; CORS-handled preflights never reach it (see /metrics).
    metrics.observe(request, response.status_code, duration_s)
    response.headers["X-Response-Time-Ms"] = str(duration_ms)
    # Security headers on every API response (P2-21). This app serves only the
    # API, so the tight CSP cannot break frontend assets; browsers ignore CSP
    # on non-document (fetch/XHR) responses anyway.
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Content-Security-Policy", "default-src 'self'")
    return response


# Middleware registration order (documented order:
# CORS -> request_id -> auth -> body_rate -> router).
# Starlette PREPENDS every add_middleware, so the LAST registered runs
# OUTERMOST. The decorators above register in definition order
# (body_size_and_rate_limit -> auth_context -> request_id) and CORS is added
# last, which yields the documented runtime order:
#   CORS -> request_id -> auth -> body_size_and_rate_limit -> router
# Because request_id wraps auth and body_rate, the 401/413/429 responses they
# return all pass back through it and carry X-Request-ID + error.request_id.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "Idempotency-Key", "X-Request-ID"],
)

app.include_router(health.router, prefix="/api/v1")
# Ops scrape target (audit P2-27): behind the same middleware stack as every
# other route, so it is counted, timed and request-id'd like the rest.
app.add_api_route("/metrics", metrics.metrics_endpoint, methods=["GET"])
app.include_router(profile.router, prefix="/api/v1")
app.include_router(onboarding.router, prefix="/api/v1")
app.include_router(research.router, prefix="/api/v1")
app.include_router(evidence.router, prefix="/api/v1")
app.include_router(monitor.router, prefix="/api/v1")
app.include_router(strategies.router, prefix="/api/v1")
app.include_router(documents.router, prefix="/api/v1")
app.include_router(programs.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")
app.include_router(auth.router, prefix="/api/v1")
app.include_router(notifications.router, prefix="/api/v1")
app.include_router(applications.router, prefix="/api/v1")
app.include_router(scholarships.router, prefix="/api/v1")
