from app.core.errors import PermissionDeniedError


class AppMismatchError(PermissionDeniedError):
    """The path `{key}` is not the calling app (`X-MM-App`)."""

    code = "app_mismatch"


class JoinClosedError(PermissionDeniedError):
    code = "join_closed"


class JoinApprovalUnavailableError(PermissionDeniedError):
    """CLI `create-account --join` only: `approval` apps need a staff decision."""

    code = "join_approval_unavailable"


class JoinUnavailableError(PermissionDeniedError):
    """`open` app without a usable default role (NULL, missing, or holding `is_admin`)."""

    code = "join_unavailable"


class JoinRejectedError(PermissionDeniedError):
    """A join request was rejected recently (cooldown). No reason, decider or date is revealed."""

    code = "join_rejected"


class JoinQueueFullError(PermissionDeniedError):
    """The app's pending join-request queue is at its cap. No count or cap is revealed."""

    code = "join_queue_full"
