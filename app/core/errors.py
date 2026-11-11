"""Domain errors and the shared JSON error envelope.

Services raise `AppError` subclasses; the handlers below turn them into
`{"error": {"code": ..., "message": ..., "details": ...}}` responses.
"""

from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response


class AppError(Exception):
    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "bad_request"

    def __init__(
        self, message: str, details: Any = None, headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details
        self.headers = headers


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class UnprocessableError(AppError):
    """A well-formed request the data refuses; `details` = `[{"field", "reason"}]`, no values.

    Exception: a detail may carry a catalog `key` that already passed format validation (the
    unknown permission keys of a role PUT); never a free-form or secret value.
    """

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "unprocessable"


class PermissionDeniedError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "permission_denied"


class RateLimitedError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"

    def __init__(self, retry_after: int) -> None:
        super().__init__(
            "Too many requests",
            details={"retry_after": retry_after},
            headers={"Retry-After": str(retry_after)},
        )


def _envelope(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


async def _app_error_handler(_: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, AppError):  # pragma: no cover
        raise exc
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(exc.code, exc.message, exc.details),
        headers=exc.headers,
    )


async def _validation_error_handler(_: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):  # pragma: no cover
        raise exc
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content=_envelope(
            "validation_error",
            "Request validation failed",
            # Never echo submitted values (`input`, `ctx`): they may hold passwords or emails.
            jsonable_encoder(
                [{"type": e["type"], "loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
            ),
        ),
    )


_HTTP_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "permission_denied",
    404: "not_found",
    405: "method_not_allowed",
    406: "not_acceptable",
    413: "payload_too_large",
    415: "unsupported_media_type",
    429: "rate_limited",
}


async def _http_exception_handler(_: Request, exc: Exception) -> Response:
    """Framework errors (unknown path, 405, ...) in the R6 envelope; never echoes `exc.detail`."""
    if not isinstance(exc, StarletteHTTPException):  # pragma: no cover
        raise exc
    status_code = exc.status_code
    if status_code in {204, 304}:
        return Response(status_code=status_code, headers=exc.headers)
    try:
        message = HTTPStatus(status_code).phrase
    except ValueError:
        message = "HTTP error"
    return JSONResponse(
        status_code=status_code,
        content=_envelope(_HTTP_CODES.get(status_code, "http_error"), message, None),
        headers=exc.headers,
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
