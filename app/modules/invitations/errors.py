from app.core.errors import PermissionDeniedError, UnprocessableError


class OrgInviteForbiddenError(PermissionDeniedError):
    """The caller lacks `<app>:org:invite` in that org."""

    code = "org_invite_forbidden"


class OrgInviteUnavailableError(PermissionDeniedError):
    """The app's role setup does not allow invitations (S1 guard, ADR-0014)."""

    code = "org_invite_unavailable"


class InviteRoleForbiddenError(PermissionDeniedError):
    """The role holds an `is_admin` permission or more than the inviter holds in the org."""

    code = "invite_role_forbidden"


class InvitationLimitReachedError(PermissionDeniedError):
    """The inviter's daily cap or the org's pending cap is reached."""

    code = "invitation_limit_reached"


class InvitationEmailMismatchError(PermissionDeniedError):
    """A live token that belongs to another email address; the invitation is not consumed."""

    code = "invitation_email_mismatch"


class UnknownRoleError(UnprocessableError):
    """The role key is not a role of the calling app."""

    code = "unknown_role"
