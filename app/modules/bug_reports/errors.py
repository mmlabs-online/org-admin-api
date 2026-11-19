from fastapi import status

from app.core.errors import AppError, RateLimitedError


class BugReportDailyLimitError(RateLimitedError):
    """The user filed the maximum number of reports in the last 24 h."""

    code = "bug_report_daily_limit"


class BugReportOpenLimitError(AppError):
    """The user has the maximum number of open reports in this app (clears when staff close one)."""

    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "bug_report_open_limit"

    def __init__(self, limit: int) -> None:
        super().__init__("Too many open bug reports", details={"limit": limit})
