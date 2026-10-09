import logging
import uuid
from typing import Any

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.runcontext import request_id_scope

log = logging.getLogger(__name__)


class AppError(HTTPException):
    def __init__(self, status_code: int, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(status_code=status_code, detail=message)
        self.code = code
        self.details = details or {}


def error_payload(code: str, message: str, details: dict[str, Any], request_id: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details, "request_id": request_id}}


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=error_payload(exc.code, str(exc.detail), exc.details, request.state.request_id),
    )


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=error_payload(
            "VALIDATION_ERROR",
            "Request validation failed",
            {"errors": exc.errors()},
            request.state.request_id,
        ),
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Render an unexpected exception as a correlated 500 (audit P2-27).

    Two things must happen here, because ServerErrorMiddleware invokes this
    handler AFTER the exception has unwound the request-id middleware scope:

    * re-enter the ``request_id`` scope so the traceback log line carries the
      same id the client will see (``logging.exception`` picks up the live
      exception being handled), and
    * stamp ``X-Request-ID`` on the response itself — the middleware that
      normally sets it never sees a response that was produced by the
      server-error path.
    """
    request_id = getattr(request.state, "request_id", "") or ""
    with request_id_scope(request_id):
        log.exception(
            "unhandled error: %s %s returned 500",
            request.method,
            request.url.path,
        )
    response = JSONResponse(
        status_code=500,
        content=error_payload("INTERNAL_ERROR", "Unexpected server error", {}, request_id),
    )
    if request_id:
        response.headers["X-Request-ID"] = request_id
    return response


def new_request_id() -> str:
    return str(uuid.uuid4())
