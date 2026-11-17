from app.core.errors import PermissionDeniedError


class OrgCreateForbiddenError(PermissionDeniedError):
    """The caller lacks the app-scope permission `<app>:org:create`."""

    code = "org_create_forbidden"


class OrgCreateUnavailableError(PermissionDeniedError):
    """No usable org owner role (unset, missing, or holding an `is_admin` permission)."""

    code = "org_create_unavailable"


class OrgLimitReachedError(PermissionDeniedError):
    """The user created `ORG_CREATE_MAX_PER_USER` organizations already."""

    code = "org_limit_reached"
