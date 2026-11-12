from fastapi import status

from app.core.errors import AppError, ConflictError, PermissionDeniedError


class EmailTakenError(ConflictError):
    code = "email_taken"


class WeakPasswordError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "weak_password"


class TurnstileFailedError(PermissionDeniedError):
    code = "turnstile_failed"


class InvalidCredentialsError(AppError):
    """One message for unknown email, wrong password and disabled user (D11)."""

    status_code = status.HTTP_401_UNAUTHORIZED
    code = "invalid_credentials"


class TurnstileRequiredError(PermissionDeniedError):
    code = "turnstile_required"


class AppHeaderRequiredError(PermissionDeniedError):
    code = "app_header_required"


class OriginNotAllowedError(PermissionDeniedError):
    code = "origin_not_allowed"
