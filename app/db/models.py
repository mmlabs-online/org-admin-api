"""Import every module's models here so Alembic sees the full metadata."""

from app.db.base import Base
from app.modules.app_credentials.models import AppCredential
from app.modules.audit.models import AuditLog
from app.modules.auth.models import EmailSendLog, EmailToken, LoginFailure
from app.modules.bug_reports.models import BugReport
from app.modules.deployer_credentials.models import DeployerCredential
from app.modules.entitlements.models import OrgEntitlement
from app.modules.invitations.models import OrgInvitation
from app.modules.join_requests.models import AppJoinRequest
from app.modules.organizations.models import Organization
from app.modules.rbac.models import App, Permission, Role, RoleAssignment, RolePermission
from app.modules.releases.models import AppRelease
from app.modules.sessions.models import AccountSession, DeviceSession
from app.modules.usage.models import UsageMetric
from app.modules.users.models import User

__all__ = [
    "AccountSession",
    "App",
    "AppCredential",
    "AppJoinRequest",
    "AppRelease",
    "AuditLog",
    "Base",
    "BugReport",
    "DeployerCredential",
    "DeviceSession",
    "EmailSendLog",
    "EmailToken",
    "LoginFailure",
    "OrgEntitlement",
    "OrgInvitation",
    "Organization",
    "Permission",
    "Role",
    "RoleAssignment",
    "RolePermission",
    "UsageMetric",
    "User",
]
