from app.core.errors import ConflictError


class ReleaseNotPendingError(ConflictError):
    """The record is `deployed` or `failed` and the app write differs from it (ADR-0021 §4)."""

    code = "release_not_pending"
