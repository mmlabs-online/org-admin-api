from app.core.errors import NotFoundError, PermissionDeniedError


class OrgCreateForbiddenError(PermissionDeniedError):
    """The caller lacks the app-scope permission `<app>:org:create`."""

    code = "org_create_forbidden"


class OrgCreateUnavailableError(PermissionDeniedError):
    """No usable org owner role (unset, missing, or holding an `is_admin` permission)."""

    code = "org_create_unavailable"


class OrgLimitReachedError(PermissionDeniedError):
    """The user created `ORG_CREATE_MAX_PER_USER` organizations already."""

    code = "org_limit_reached"


class OrgNotVisibleToAppError(NotFoundError):
    """App-credential read: one 404 for an unknown, unrelated or suspended org (USR-29)."""

    code = "org_access_denied"
