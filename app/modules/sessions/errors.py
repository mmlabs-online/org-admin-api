from fastapi import status

from app.core.errors import AppError


class SessionInvalidError(AppError):
    """No, unknown, revoked, expired or duplicated `mm_device` cookie, or reuse after grace."""

    status_code = status.HTTP_401_UNAUTHORIZED
    code = "session_invalid"
