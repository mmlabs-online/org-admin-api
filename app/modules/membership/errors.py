from app.core.errors import PermissionDeniedError


class AppMismatchError(PermissionDeniedError):
    """The path `{key}` is not the calling app (`X-MM-App`)."""

    code = "app_mismatch"


class JoinClosedError(PermissionDeniedError):
    code = "join_closed"


class JoinApprovalUnavailableError(PermissionDeniedError):
    """`approval` apps until USR-03g."""

    code = "join_approval_unavailable"


class JoinUnavailableError(PermissionDeniedError):
    """`open` app without a usable default role (NULL, missing, or holding `is_admin`)."""

    code = "join_unavailable"
