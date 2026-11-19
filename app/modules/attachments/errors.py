"""Typed Drive failures. The code is the only information carried (R5/R11, ADR-0018)."""

from enum import StrEnum


class DriveErrorCode(StrEnum):
    NOT_CONFIGURED = "not_configured"
    AUTH_FAILED = "auth_failed"
    QUOTA_EXCEEDED = "quota_exceeded"
    RATE_LIMITED = "rate_limited"
    NOT_FOUND = "not_found"
    TOO_LARGE = "too_large"
    REJECTED = "rejected"
    UNAVAILABLE = "unavailable"
    INVALID_INPUT = "invalid_input"


class DriveError(Exception):
    """Not an `AppError`: the caller decides the per-attachment outcome (spec OQ35)."""

    def __init__(self, code: DriveErrorCode) -> None:
        super().__init__(code.value)
        self.code = code

    def __str__(self) -> str:
        return self.code.value
