import uuid
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.rbac.models import App, Permission, Role, RoleAssignment, RolePermission
from tests.rbac_factories import assign, grant, make_app, make_permission, make_role, make_user


async def test_seed_admin_app(db_session: AsyncSession) -> None:
    app = await db_session.scalar(select(App).where(App.key == "admin"))
    assert app is not None
    assert app.join_policy == "closed"
    assert app.org_mode == "none"
    assert app.origins == []
    assert app.status == "active"
    assert app.default_role_id is None

    wildcard = await db_session.scalar(
        select(Permission).where(Permission.app_id == app.id, Permission.key == "admin:*")
    )
    assert wildcard is not None and wildcard.is_admin

    role = await db_session.scalar(
        select(Role).where(Role.app_id == app.id, Role.key == "admin:super-admin")
    )
    assert role is not None and role.is_system
    held = await db_session.scalars(
        select(Permission.key)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .where(RolePermission.role_id == role.id)
    )
    assert set(held) == {"admin:*"}
    assert await db_session.scalar(select(Role).where(Role.key == "admin:member")) is None


async def test_app_defaults(db_session: AsyncSession) -> None:
    # Raw SQL so the server defaults (not the Python ones) are exercised.
    await db_session.execute(
        text("INSERT INTO apps (id, key, name) VALUES (:id, 'stoktok', 'Stoktok')"),
        {"id": uuid.uuid4()},
    )
    app = await db_session.scalar(select(App).where(App.key == "stoktok"))
    assert app is not None
    assert app.join_policy == "open"
    assert app.org_mode == "none"
    assert app.origins == []
    assert app.status == "active"
    assert app.default_role_id is None


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        ({"join_policy": "x"}, IntegrityError),
        ({"org_mode": "x"}, IntegrityError),
        ({"status": "x"}, IntegrityError),
        ({"key": "Bad"}, IntegrityError),
        ({"key": "a"}, IntegrityError),
        ({"key": "st:ok"}, IntegrityError),
        ({"key": "1app"}, IntegrityError),
        ({"key": "a" * 33}, DBAPIError),  # varchar(32): value too long
    ],
)
async def test_app_check_constraints(
    db_session: AsyncSession, fields: dict[str, Any], error: type[Exception]
) -> None:
    values: dict[str, Any] = {"key": "stoktok", "name": "Stoktok", **fields}
    with pytest.raises(error):
        async with db_session.begin_nested():
            db_session.add(App(**values))
            await db_session.flush()


@pytest.mark.parametrize(
    ("key", "is_admin"),
    [("stoktok:portfolio:read", False), ("stoktok:org:create", False), ("stoktok:*", True)],
)
async def test_permission_key_format_valid(
    db_session: AsyncSession, key: str, is_admin: bool
) -> None:
    app = await make_app(db_session)
    perm = await make_permission(db_session, app, key, is_admin=is_admin)
    assert perm.id is not None


@pytest.mark.parametrize(
    "key",
    [
        "*",
        "stoktok",
        "stoktok:a",
        "stoktok:*:*",
        "stoktok:a:*",
        "stoktok:a:b:c",
        "Stoktok:a:b",
        "stoktok:a_b:c",
        "stoktok::read",
    ],
)
async def test_permission_key_format_invalid(db_session: AsyncSession, key: str) -> None:
    app = await make_app(db_session)
    with pytest.raises(IntegrityError, match="ck_permissions_key_format"):
        async with db_session.begin_nested():
            await make_permission(db_session, app, key, is_admin=True)


async def test_wildcard_permission_must_be_admin(db_session: AsyncSession) -> None:
    app = await make_app(db_session)
    with pytest.raises(IntegrityError, match="ck_permissions_wildcard_is_admin"):
        async with db_session.begin_nested():
            await make_permission(db_session, app, "stoktok:*", is_admin=False)


@pytest.mark.parametrize("key", ["stoktok:member", "stoktok:super-admin"])
async def test_role_key_format_valid(db_session: AsyncSession, key: str) -> None:
    app = await make_app(db_session)
    assert (await make_role(db_session, app, key)).id is not None


@pytest.mark.parametrize("key", ["member", "stoktok:*", "stoktok:a:b", "STOKTOK:member"])
async def test_role_key_format_invalid(db_session: AsyncSession, key: str) -> None:
    app = await make_app(db_session)
    with pytest.raises(IntegrityError, match="ck_roles_key_format"):
        async with db_session.begin_nested():
            await make_role(db_session, app, key)


async def test_duplicate_permission_key_rejected(db_session: AsyncSession) -> None:
    app = await make_app(db_session)
    await make_permission(db_session, app, "stoktok:portfolio:read")
    with pytest.raises(IntegrityError, match="uq_permissions_app_id_key"):
        async with db_session.begin_nested():
            await make_permission(db_session, app, "stoktok:portfolio:read")


async def test_duplicate_role_key_rejected(db_session: AsyncSession) -> None:
    app = await make_app(db_session)
    await make_role(db_session, app, "stoktok:member")
    with pytest.raises(IntegrityError, match="uq_roles_app_id_key"):
        async with db_session.begin_nested():
            await make_role(db_session, app, "stoktok:member")


async def test_role_permission_cross_app_rejected(db_session: AsyncSession) -> None:
    stoktok = await make_app(db_session, "stoktok")
    calechip = await make_app(db_session, "calechip")
    role = await make_role(db_session, stoktok, "stoktok:member")
    foreign = await make_permission(db_session, calechip, "calechip:shop:read")
    own = await make_permission(db_session, stoktok, "stoktok:portfolio:read")

    for app_id in (stoktok.id, calechip.id):
        with pytest.raises(IntegrityError, match="same_app"):
            async with db_session.begin_nested():
                db_session.add(
                    RolePermission(role_id=role.id, permission_id=foreign.id, app_id=app_id)
                )
                await db_session.flush()

    await grant(db_session, role, own)


async def test_default_role_must_belong_to_same_app(db_session: AsyncSession) -> None:
    stoktok = await make_app(db_session, "stoktok")
    calechip = await make_app(db_session, "calechip")
    foreign_role = await make_role(db_session, calechip, "calechip:member")
    own_role = await make_role(db_session, stoktok, "stoktok:member")

    with pytest.raises(IntegrityError, match="fk_apps_default_role_same_app"):
        async with db_session.begin_nested():
            await db_session.execute(
                text("UPDATE apps SET default_role_id = :r WHERE id = :a"),
                {"r": foreign_role.id, "a": stoktok.id},
            )

    stoktok.default_role_id = own_role.id
    await db_session.flush()
    stoktok.default_role_id = None
    await db_session.flush()


async def test_duplicate_app_scope_assignment_rejected(db_session: AsyncSession) -> None:
    app = await make_app(db_session)
    role = await make_role(db_session, app, "stoktok:member")
    user = await make_user(db_session)
    await assign(db_session, user, role)
    with pytest.raises(IntegrityError, match="uq_role_assignments_user_role_org"):
        async with db_session.begin_nested():
            await assign(db_session, user, role)


async def test_org_scoped_assignments_unique_per_org(db_session: AsyncSession) -> None:
    app = await make_app(db_session)
    role = await make_role(db_session, app, "stoktok:member")
    user = await make_user(db_session)
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    await assign(db_session, user, role)
    await assign(db_session, user, role, org_id=org_a)
    await assign(db_session, user, role, org_id=org_b)
    with pytest.raises(IntegrityError, match="uq_role_assignments_user_role_org"):
        async with db_session.begin_nested():
            await assign(db_session, user, role, org_id=org_a)


async def _count(session: AsyncSession, model: Any, app_id: uuid.UUID) -> int:
    return (
        await session.scalar(select(func.count()).select_from(model).where(model.app_id == app_id))
        or 0
    )


async def test_delete_app_cascades_catalog(db_session: AsyncSession) -> None:
    app = await make_app(db_session)
    p1 = await make_permission(db_session, app, "stoktok:portfolio:read")
    p2 = await make_permission(db_session, app, "stoktok:portfolio:write")
    role = await make_role(db_session, app, "stoktok:member")
    await grant(db_session, role, p1, p2)
    app.default_role_id = role.id
    await db_session.flush()

    await db_session.delete(app)
    await db_session.flush()

    for model in (Permission, Role, RolePermission):
        assert await _count(db_session, model, app.id) == 0


async def test_role_with_assignments_cannot_be_deleted(db_session: AsyncSession) -> None:
    app = await make_app(db_session)
    role = await make_role(db_session, app, "stoktok:member")
    await assign(db_session, await make_user(db_session), role)
    with pytest.raises(IntegrityError, match="fk_role_assignments_role_id_roles"):
        async with db_session.begin_nested():
            await db_session.delete(role)
            await db_session.flush()


async def test_delete_user_cascades_and_nulls_granted_by(db_session: AsyncSession) -> None:
    app = await make_app(db_session)
    role = await make_role(db_session, app, "stoktok:member")
    granter = await make_user(db_session)
    user = await make_user(db_session)
    granter_ra = await assign(db_session, granter, role)
    user_ra = await assign(db_session, user, role, granted_by=granter.id)

    await db_session.delete(granter)
    await db_session.flush()
    rows = await db_session.execute(
        select(RoleAssignment.id, RoleAssignment.granted_by).where(
            RoleAssignment.id.in_([granter_ra.id, user_ra.id])
        )
    )
    assert rows.all() == [(user_ra.id, None)]
