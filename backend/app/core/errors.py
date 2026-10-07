import uuid
from typing import Any

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


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
    return JSONResponse(
        status_code=500,
        content=error_payload("INTERNAL_ERROR", "Unexpected server error", {}, request.state.request_id),
    )


def new_request_id() -> str:
    return str(uuid.uuid4())
