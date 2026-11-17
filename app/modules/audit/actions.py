"""The audit catalogue (ADM-004): one `AuditAction` per state change, past tense."""

from enum import StrEnum


class ActorKind(StrEnum):
    USER = "user"
    SYSTEM = "system"
    ANONYMOUS = "anonymous"  # an unauthenticated caller: failed logins, cookie-only events


class AuditOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    DENIED = "denied"


class AuditTarget(StrEnum):
    ORGANIZATION = "organization"
    USER = "user"
    APP = "app"
    PERMISSION = "permission"
    ROLE = "role"
    ROLE_ASSIGNMENT = "role_assignment"
    DEVICE_SESSION = "device_session"
    INVITATION = "invitation"


class AuditAction(StrEnum):
    ORGANIZATION_CREATED = "organization.created"
    ORGANIZATION_UPDATED = "organization.updated"
    ORGANIZATION_DELETED = "organization.deleted"
    ORGANIZATION_INVITATION_CREATED = "organization.invitation_created"
    ORGANIZATION_INVITATION_ACCEPTED = "organization.invitation_accepted"
    ORGANIZATION_INVITATION_REVOKED = "organization.invitation_revoked"
    USER_CREATED = "user.created"
    USER_DISABLED = "user.disabled"
    USER_ENABLED = "user.enabled"
    APP_CREATED = "app.created"
    APP_UPDATED = "app.updated"
    APP_DELETED = "app.deleted"
    APP_REGISTERED = "app.registered"
    APP_ADMIN_ORIGINS_SET = "app.admin_origins_set"
    APP_JOINED = "app.joined"
    PERMISSION_CREATED = "permission.created"
    PERMISSION_UPDATED = "permission.updated"
    PERMISSION_DELETED = "permission.deleted"
    ROLE_CREATED = "role.created"
    ROLE_UPDATED = "role.updated"
    ROLE_DELETED = "role.deleted"
    ROLE_PERMISSIONS_REPLACED = "role.permissions_replaced"
    ROLE_ASSIGNMENT_GRANTED = "role_assignment.granted"
    ROLE_ASSIGNMENT_REVOKED = "role_assignment.revoked"
    EMAIL_RETENTION_PURGED = "email_retention.purged"
    AUDIT_LOG_PURGED = "audit_log.purged"
    AUTH_LOGIN_SUCCEEDED = "auth.login_succeeded"
    AUTH_LOGIN_FAILED = "auth.login_failed"
    AUTH_LOGGED_OUT = "auth.logged_out"
    AUTH_LOGGED_OUT_ALL = "auth.logged_out_all"
    DEVICE_SESSION_REUSE_REVOKED = "device_session.reuse_revoked"
    AUTH_PASSWORD_RESET_COMPLETED = "auth.password_reset_completed"  # noqa: S105 (a name)
    AUTH_EMAIL_VERIFIED = "auth.email_verified"
