import asyncio
import logging
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import (
    admin,
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
    strategies,
)
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
from app.core.security import bearer_token, decode_token, reset_request_user, set_request_user
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


MAX_BODY_BYTES = 1_000_000
RATE_LIMIT_WINDOW_SECONDS = 60

# Expensive endpoints get the per-IP budget (TEST_PLAN §Security: "rate limit
# expensive endpoints"). Static paths are matched exactly; POST
# /api/v1/evidence/{evidence_id}/recheck is dynamic and matched by pattern
# below, so every evidence recheck counts against the same budget.
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


def _is_rate_limited(request: Request) -> bool:
    if request.method != "POST":
        return False
    path = request.url.path
    if path in _RATE_LIMITED_PATHS:
        return True
    # POST /api/v1/evidence/{evidence_id}/recheck triggers provider work.
    return path.startswith("/api/v1/evidence/") and path.endswith("/recheck")


_rate_counters: dict[str, tuple[float, int]] = {}


@app.middleware("http")
async def body_size_and_rate_limit(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    if request.method in ("POST", "PATCH", "PUT"):
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
            return JSONResponse(
                status_code=413,
                content=error_payload(
                    "PAYLOAD_TOO_LARGE",
                    "Request body too large",
                    {},
                    getattr(request.state, "request_id", ""),
                ),
            )
    if _is_rate_limited(request):
        key = request.client.host if request.client else "unknown"
        now = time.time()
        window_start, count = _rate_counters.get(key, (now, 0))
        if now - window_start > RATE_LIMIT_WINDOW_SECONDS:
            window_start, count = now, 0
        count += 1
        _rate_counters[key] = (window_start, count)
        if count > settings.rate_limit_per_minute:
            return JSONResponse(
                status_code=429,
                content=error_payload(
                    "RATE_LIMITED",
                    "Too many requests",
                    {},
                    getattr(request.state, "request_id", ""),
                ),
            )
    return await call_next(request)


@app.middleware("http")
async def auth_context_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Decode bearer token into request ContextVars (see app/core/security.py).

    No token → anonymous/demo user. Invalid/expired token → 401 so clients
    drop the stale session instead of silently writing to the wrong user.
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
        tokens = set_request_user(uuid.UUID(claims["sub"]), claims["role"])
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
        response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    duration_ms = int((time.perf_counter() - start) * 1000)
    response.headers["X-Response-Time-Ms"] = str(duration_ms)
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
