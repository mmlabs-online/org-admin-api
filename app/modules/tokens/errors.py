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
    """The app does not use organizations (`org_mode = none`)."""

    code = "org_not_supported"


class OrgAccessDeniedError(PermissionDeniedError):
    """One answer for an unknown, suspended or not-a-member org (no existence oracle)."""

    code = "org_access_denied"
