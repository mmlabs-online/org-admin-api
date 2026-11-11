"""Insert RBAC rows through the test session (flush only; the test transaction rolls back)."""

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.rbac.models import App, Permission, Role, RoleAssignment, RolePermission
from app.modules.users.models import User


async def make_user(session: AsyncSession) -> User:
    user = User(email=f"{uuid.uuid4().hex[:12]}@example.test", password_hash="h")
    session.add(user)
    await session.flush()
    return user


async def make_app(session: AsyncSession, key: str = "stoktok", **kwargs: object) -> App:
    app = App(key=key, name=key.title(), **kwargs)
    session.add(app)
    await session.flush()
    return app


async def make_permission(
    session: AsyncSession, app: App, key: str, *, is_admin: bool = False
) -> Permission:
    perm = Permission(app_id=app.id, key=key, is_admin=is_admin)
    session.add(perm)
    await session.flush()
    return perm


async def make_role(session: AsyncSession, app: App, key: str) -> Role:
    role = Role(app_id=app.id, key=key, name=key)
    session.add(role)
    await session.flush()
    return role


async def grant(session: AsyncSession, role: Role, *perms: Permission) -> None:
    session.add_all(
        RolePermission(role_id=role.id, permission_id=p.id, app_id=role.app_id) for p in perms
    )
    await session.flush()


async def assign(
    session: AsyncSession,
    user: User,
    role: Role,
    *,
    org_id: uuid.UUID | None = None,
    expires_at: datetime | None = None,
    granted_by: uuid.UUID | None = None,
) -> RoleAssignment:
    ra = RoleAssignment(
        user_id=user.id,
        role_id=role.id,
        org_id=org_id,
        expires_at=expires_at,
        granted_by=granted_by,
    )
    session.add(ra)
    await session.flush()
    return ra
