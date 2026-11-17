"""Import every module's models here so Alembic sees the full metadata."""

from app.db.base import Base
from app.modules.app_credentials.models import AppCredential
from app.modules.audit.models import AuditLog
from app.modules.auth.models import EmailSendLog, EmailToken, LoginFailure
from app.modules.invitations.models import OrgInvitation
from app.modules.organizations.models import Organization
from app.modules.rbac.models import App, Permission, Role, RoleAssignment, RolePermission
from app.modules.sessions.models import AccountSession, DeviceSession
from app.modules.users.models import User

__all__ = [
    "AccountSession",
    "App",
    "AppCredential",
    "AuditLog",
    "Base",
    "DeviceSession",
    "EmailSendLog",
    "EmailToken",
    "LoginFailure",
    "OrgInvitation",
    "Organization",
    "Permission",
    "Role",
    "RoleAssignment",
    "RolePermission",
    "User",
]
