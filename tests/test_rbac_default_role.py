import uuid

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.modules.rbac.models import App, Permission, Role, RolePermission
from app.modules.rbac.repository import RbacRepository
from app.modules.rbac.service import RoleCatalogService
from tests.rbac_factories import make_app, make_permission, make_role


async def _held(session: AsyncSession, role_key: str) -> set[str]:
    result = await session.scalars(
        select(Permission.key)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(Role, Role.id == RolePermission.role_id)
        .where(Role.key == role_key)
    )
    return set(result)


async def _setup(session: AsyncSession) -> App:
    app = await make_app(session)
    await make_permission(session, app, "stoktok:portfolio:read")
    await make_permission(session, app, "stoktok:portfolio:write")
    await make_permission(session, app, "stoktok:roles:manage", is_admin=True)
    await make_permission(session, app, "stoktok:*", is_admin=True)
    return app


async def test_build_creates_member_with_non_admin_permissions(db_session: AsyncSession) -> None:
    app = await _setup(db_session)
    result = await RoleCatalogService(db_session).build_default_role("stoktok")

    assert result.role_key == "stoktok:member"
    assert result.created is True
    assert result.added == ["stoktok:portfolio:read", "stoktok:portfolio:write"]
    assert result.removed == []
    role = await db_session.scalar(select(Role).where(Role.key == "stoktok:member"))
    assert role is not None and role.is_system
    assert await _held(db_session, "stoktok:member") == {
        "stoktok:portfolio:read",
        "stoktok:portfolio:write",
    }
    await db_session.refresh(app)
    assert app.default_role_id == role.id


async def test_build_is_idempotent(db_session: AsyncSession) -> None:
    await _setup(db_session)
    service = RoleCatalogService(db_session)
    await service.build_default_role("stoktok")
    count = select(func.count()).select_from(RolePermission)
    before = await db_session.scalar(count)

    result = await service.build_default_role("stoktok")

    assert result.created is False
    assert result.added == result.removed == []
    assert await db_session.scalar(count) == before


async def test_build_reflects_data_changes(db_session: AsyncSession) -> None:
    app = await _setup(db_session)
    service = RoleCatalogService(db_session)
    await service.build_default_role("stoktok")

    await make_permission(db_session, app, "stoktok:news:read")
    p1 = await db_session.scalar(
        select(Permission).where(Permission.key == "stoktok:portfolio:read")
    )
    assert p1 is not None
    p1.is_admin = True
    await db_session.flush()

    result = await service.build_default_role("stoktok")

    assert result.added == ["stoktok:news:read"]
    assert result.removed == ["stoktok:portfolio:read"]
    assert await _held(db_session, "stoktok:member") == {
        "stoktok:news:read",
        "stoktok:portfolio:write",
    }


async def test_build_keeps_existing_default_role(db_session: AsyncSession) -> None:
    app = await _setup(db_session)
    other = await make_role(db_session, app, "stoktok:viewer")
    app.default_role_id = other.id
    await db_session.flush()

    await RoleCatalogService(db_session).build_default_role("stoktok")

    await db_session.refresh(app)
    assert app.default_role_id == other.id
    assert await db_session.scalar(select(Role).where(Role.key == "stoktok:member")) is not None


async def test_build_unknown_app_raises_not_found(db_session: AsyncSession) -> None:
    with pytest.raises(NotFoundError):
        await RoleCatalogService(db_session).build_default_role("nope")


async def test_new_permission_not_added_until_rebuild(db_session: AsyncSession) -> None:
    app = await _setup(db_session)
    await RoleCatalogService(db_session).build_default_role("stoktok")
    await make_permission(db_session, app, "stoktok:news:read")
    assert "stoktok:news:read" not in await _held(db_session, "stoktok:member")


async def test_build_refuses_admin_app(db_session: AsyncSession) -> None:
    with pytest.raises(ConflictError):
        await RoleCatalogService(db_session).build_default_role("admin")
    assert await db_session.scalar(select(Role).where(Role.key == "admin:member")) is None
    admin = await db_session.scalar(select(App).where(App.key == "admin"))
    assert admin is not None and admin.default_role_id is None


async def test_build_role_insert_race_is_409(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = await _setup(db_session)
    await make_role(db_session, app, "stoktok:member")  # "created by a concurrent request"

    async def _missing(self: RbacRepository, app_id: uuid.UUID, key: str) -> Role | None:
        return None

    monkeypatch.setattr(RbacRepository, "get_role_by_key", _missing)
    with pytest.raises(ConflictError):
        await RoleCatalogService(db_session).build_default_role("stoktok")
    # The savepoint kept the session usable.
    assert await db_session.scalar(select(func.count()).select_from(App)) is not None


async def test_build_does_not_overwrite_concurrent_default(db_session: AsyncSession) -> None:
    app = await _setup(db_session)
    other = await make_role(db_session, app, "stoktok:viewer")
    assert app.default_role_id is None
    # A concurrent PATCH sets the default; the loaded `app` object is now stale.
    await db_session.execute(
        update(App)
        .where(App.id == app.id)
        .values(default_role_id=other.id)
        .execution_options(synchronize_session=False)
    )
    assert app.default_role_id is None

    await RoleCatalogService(db_session).build_default_role("stoktok")

    await db_session.refresh(app)
    assert app.default_role_id == other.id


async def test_build_permission_fk_race_is_409(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = await _setup(db_session)
    # "Deleted by a concurrent request": a permission the builder read but the DB no longer has.
    ghost = Permission(id=uuid.uuid4(), app_id=app.id, key="stoktok:x:y")

    async def _ghost(
        self: RbacRepository, app_id: uuid.UUID, *, is_admin: bool | None = None
    ) -> list[Permission]:
        return [ghost]

    monkeypatch.setattr(RbacRepository, "list_permissions", _ghost)
    with pytest.raises(ConflictError):
        await RoleCatalogService(db_session).build_default_role("stoktok")
    # The savepoint kept the session usable.
    assert await db_session.scalar(select(func.count()).select_from(App)) == 2
