from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError


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


class OrgRemoveMemberForbiddenError(PermissionDeniedError):
    """The caller lacks `<app>:org:remove-member` in that org (USR-30)."""

    code = "org_remove_member_forbidden"


class OrgMemberRemoveUnavailableError(PermissionDeniedError):
    """The app has no usable org owner role, so the last-owner rule cannot be applied."""

    code = "org_member_remove_unavailable"


class SelfRemovalForbiddenError(PermissionDeniedError):
    """The caller named themselves as the target."""

    code = "self_removal_forbidden"


class MemberOutranksCallerError(PermissionDeniedError):
    """The target holds an `is_admin` permission or more than the caller holds in the org."""

    code = "member_outranks_caller"


class MemberNotFoundError(NotFoundError):
    """The target holds no non-expired role of the app in that org."""

    code = "member_not_found"


class LastOrgOwnerError(ConflictError):
    """The removal would leave the org without an owner."""

    code = "last_org_owner"
