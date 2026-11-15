from fastapi import status

from app.core.errors import AppError, PermissionDeniedError, UnprocessableError


class AudienceMismatchError(PermissionDeniedError):
    code = "audience_mismatch"


class EmailUnverifiedError(PermissionDeniedError):
    code = "email_unverified"


class NotMemberError(AppError):
    """`details = {"join_available": bool}`."""

    status_code = status.HTTP_403_FORBIDDEN
    code = "not_member"


class OrgNotSupportedError(UnprocessableError):
    """`org_id` until USR-06."""

    code = "org_not_supported"
