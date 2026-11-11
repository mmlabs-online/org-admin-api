"""Import every module's models here so Alembic sees the full metadata."""

from app.db.base import Base
from app.modules.organizations.models import Organization
from app.modules.rbac.models import App, Permission, Role, RoleAssignment, RolePermission
from app.modules.users.models import User

__all__ = [
    "App",
    "Base",
    "Organization",
    "Permission",
    "Role",
    "RoleAssignment",
    "RolePermission",
    "User",
]
