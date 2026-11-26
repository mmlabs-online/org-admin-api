from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError


class UsageMetricExistsError(ConflictError):
    code = "usage_metric_exists"


class UsageMetricNotFoundError(NotFoundError):
    """Unknown, archived or another app's metric (ADM-042)."""

    code = "usage_metric_not_found"


class UsageEventNotFoundError(NotFoundError):
    code = "usage_event_not_found"


class UsageUserDeniedError(PermissionDeniedError):
    """Unknown, non-member or disabled user: one code, so it is no existence oracle."""

    code = "usage_user_denied"


class UsageLimitExceededError(ConflictError):
    code = "usage_limit_exceeded"


class IdempotencyKeyReusedError(ConflictError):
    code = "idempotency_key_reused"


class UsageEventStateError(ConflictError):
    code = "usage_event_state_conflict"


class UsageAdjustWindowClosedError(ConflictError):
    code = "usage_adjust_window_closed"
