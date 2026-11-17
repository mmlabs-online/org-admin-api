from app.core.errors import PermissionDeniedError


class AppCredentialMismatchError(PermissionDeniedError):
    """A valid credential of another app was used (ADR-0016 §4)."""

    code = "app_credential_mismatch"


class AppCredentialAppDisabledError(PermissionDeniedError):
    """The credential's app is disabled."""

    code = "app_disabled"
