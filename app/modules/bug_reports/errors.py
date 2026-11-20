from fastapi import status

from app.core.errors import (
    AppError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitedError,
    ServiceUnavailableError,
    UnprocessableError,
)


class BugReportDailyLimitError(RateLimitedError):
    """The user filed the maximum number of reports in the last 24 h."""

    code = "bug_report_daily_limit"


class BugReportOpenLimitError(AppError):
    """The user has the maximum number of open reports in this app (clears when staff close one)."""

    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "bug_report_open_limit"

    def __init__(self, limit: int) -> None:
        super().__init__("Too many open bug reports", details={"limit": limit})


class AttachmentUploadDeniedError(PermissionDeniedError):
    """Not the reporter, other app, or unknown report: one answer (no existence oracle)."""

    code = "attachment_upload_denied"

    def __init__(self) -> None:
        super().__init__("Attachments cannot be added to this report")


class AttachmentUploadClosedError(PermissionDeniedError):
    """The reporter's own report no longer takes attachments (`report_done`, `window_expired`)."""

    code = "attachment_upload_closed"

    def __init__(self, reason: str) -> None:
        super().__init__("This report no longer accepts attachments", details={"reason": reason})


class AttachmentLimitError(ConflictError):
    code = "attachment_limit_reached"

    def __init__(self, limit: int) -> None:
        super().__init__("Attachment limit reached", details={"limit": limit})


class AttachmentIncompleteError(UnprocessableError):
    code = "attachment_incomplete"

    def __init__(self, message: str = "Attachment body is incomplete") -> None:
        super().__init__(message)


class AttachmentUploadBusyError(RateLimitedError):
    code = "attachment_upload_busy"

    def __init__(self) -> None:
        super().__init__(5)


class AttachmentUnavailableError(NotFoundError):
    code = "attachment_unavailable"

    def __init__(self) -> None:
        super().__init__("Attachment is unavailable")


class AttachmentStorageUnavailableError(ServiceUnavailableError):
    code = "attachment_storage_unavailable"

    def __init__(self, *, retryable: bool) -> None:
        super().__init__(
            "Attachment storage is unavailable",
            details={"retryable": retryable},
            headers={"Retry-After": "30"} if retryable else None,
        )
