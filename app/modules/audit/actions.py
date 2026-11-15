"""The audit catalogue (ADM-004): one `AuditAction` per state change, past tense."""

from enum import StrEnum


class ActorKind(StrEnum):
    USER = "user"
    SYSTEM = "system"
    ANONYMOUS = "anonymous"  # reserved for ADM-004b (failed logins of unknown emails)


class AuditTarget(StrEnum):
    ORGANIZATION = "organization"
    USER = "user"
    APP = "app"
    PERMISSION = "permission"
    ROLE = "role"
    ROLE_ASSIGNMENT = "role_assignment"


class AuditAction(StrEnum):
    ORGANIZATION_CREATED = "organization.created"
    ORGANIZATION_UPDATED = "organization.updated"
    ORGANIZATION_DELETED = "organization.deleted"
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
