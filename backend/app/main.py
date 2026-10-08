import asyncio
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
from app.core.security import bearer_token, decode_token, reset_request_user, set_request_user

configure_logging()
settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):  # type: ignore[no-untyped-def]
    """Start/stop background workers with the app process."""
    from app.workers import scheduler

    await scheduler.start()
    try:
        yield
    finally:
        await scheduler.stop()


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
                content={
                    "error": {
                        "code": "PAYLOAD_TOO_LARGE",
                        "message": "Request body too large",
                        "details": {},
                        "request_id": getattr(request.state, "request_id", ""),
                    }
                },
            )
    if (
        request.url.path
        in (
            "/api/v1/research/runs",
            "/api/v1/research/demo",
            "/api/v1/monitor/subscriptions",
            "/api/v1/auth/login",
            "/api/v1/auth/register",
        )
        and request.method == "POST"
    ):
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
                content={
                    "error": {
                        "code": "RATE_LIMITED",
                        "message": "Too many requests",
                        "details": {},
                        "request_id": getattr(request.state, "request_id", ""),
                    }
                },
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
    start = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    duration_ms = int((time.perf_counter() - start) * 1000)
    response.headers["X-Response-Time-Ms"] = str(duration_ms)
    return response


# CORS is added LAST so it is the OUTERMOST middleware: every response,
# including auth 401s and rate-limit 429s, carries CORS headers.
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
