from fastapi import status

from app.core.errors import AppError, ConflictError, PermissionDeniedError


class EmailTakenError(ConflictError):
    code = "email_taken"


class WeakPasswordError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "weak_password"


class TurnstileFailedError(PermissionDeniedError):
    code = "turnstile_failed"
