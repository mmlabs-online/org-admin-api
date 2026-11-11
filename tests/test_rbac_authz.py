import uuid
from collections.abc import Collection
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.rbac.authz import AuthzService, permits
from app.modules.rbac.models import Role
from tests.rbac_factories import assign, grant, make_app, make_permission, make_role, make_user

PAST = datetime(2000, 1, 1, tzinfo=UTC)
FUTURE = datetime(2999, 1, 1, tzinfo=UTC)


async def test_effective_roles_app_scope(db_session: AsyncSession) -> None:
    app = await make_app(db_session)
    r1 = await make_role(db_session, app, "stoktok:member")
    r2 = await make_role(db_session, app, "stoktok:editor")
    user = await make_user(db_session)
    await assign(db_session, user, r1)
    await assign(db_session, user, r2)
    roles = await AuthzService(db_session).effective_roles(user.id, "stoktok")
    assert roles == ["stoktok:editor", "stoktok:member"]


async def _org_setup(db_session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    app = await make_app(db_session)
    user = await make_user(db_session)
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    await assign(db_session, user, await make_role(db_session, app, "stoktok:r1"))
    await assign(db_session, user, await make_role(db_session, app, "stoktok:r2"), org_id=org_a)
    await assign(db_session, user, await make_role(db_session, app, "stoktok:r3"), org_id=org_b)
    return user.id, org_a


async def test_effective_roles_with_org(db_session: AsyncSession) -> None:
    user_id, org_a = await _org_setup(db_session)
    roles = await AuthzService(db_session).effective_roles(user_id, "stoktok", org_a)
    assert roles == ["stoktok:r1", "stoktok:r2"]


async def test_effective_roles_without_org_excludes_org_roles(db_session: AsyncSession) -> None:
    user_id, _ = await _org_setup(db_session)
    assert await AuthzService(db_session).effective_roles(user_id, "stoktok") == ["stoktok:r1"]


async def test_effective_roles_ignores_expired(db_session: AsyncSession) -> None:
    app = await make_app(db_session)
    user = await make_user(db_session)
    await assign(db_session, user, await make_role(db_session, app, "stoktok:old"), expires_at=PAST)
    await assign(
        db_session, user, await make_role(db_session, app, "stoktok:later"), expires_at=FUTURE
    )
    await assign(db_session, user, await make_role(db_session, app, "stoktok:forever"))
    roles = await AuthzService(db_session).effective_roles(user.id, "stoktok")
    assert roles == ["stoktok:forever", "stoktok:later"]


async def test_effective_roles_other_app_excluded(db_session: AsyncSession) -> None:
    stoktok = await make_app(db_session, "stoktok")
    calechip = await make_app(db_session, "calechip")
    user = await make_user(db_session)
    await assign(db_session, user, await make_role(db_session, stoktok, "stoktok:member"))
    await assign(db_session, user, await make_role(db_session, calechip, "calechip:member"))
    assert await AuthzService(db_session).effective_roles(user.id, "stoktok") == ["stoktok:member"]


async def test_effective_roles_unknown_app_is_empty(db_session: AsyncSession) -> None:
    app = await make_app(db_session)
    user = await make_user(db_session)
    await assign(db_session, user, await make_role(db_session, app, "stoktok:member"))
    assert await AuthzService(db_session).effective_roles(user.id, "nope") == []


async def test_effective_roles_deduplicated_and_sorted(db_session: AsyncSession) -> None:
    app = await make_app(db_session)
    user = await make_user(db_session)
    org_a = uuid.uuid4()
    zeta = await make_role(db_session, app, "stoktok:zeta")
    alpha = await make_role(db_session, app, "stoktok:alpha")
    await assign(db_session, user, zeta)
    await assign(db_session, user, zeta, org_id=org_a)
    await assign(db_session, user, alpha, org_id=org_a)
    roles = await AuthzService(db_session).effective_roles(user.id, "stoktok", org_a)
    assert roles == ["stoktok:alpha", "stoktok:zeta"]


async def test_effective_permissions_union_and_wildcard(db_session: AsyncSession) -> None:
    app = await make_app(db_session)
    user = await make_user(db_session)
    read = await make_permission(db_session, app, "stoktok:portfolio:read")
    write = await make_permission(db_session, app, "stoktok:portfolio:write")
    news = await make_permission(db_session, app, "stoktok:news:read")
    r1 = await make_role(db_session, app, "stoktok:r1")
    r2 = await make_role(db_session, app, "stoktok:r2")
    await grant(db_session, r1, read, write)
    await grant(db_session, r2, write, news)
    await assign(db_session, user, r1)
    await assign(db_session, user, r2)
    authz = AuthzService(db_session)
    assert await authz.effective_permissions(user.id, "stoktok") == [
        "stoktok:news:read",
        "stoktok:portfolio:read",
        "stoktok:portfolio:write",
    ]

    wildcard = await make_permission(db_session, app, "stoktok:*", is_admin=True)
    admin = await make_role(db_session, app, "stoktok:admin")
    await grant(db_session, admin, wildcard)
    await assign(db_session, user, admin)
    perms = await authz.effective_permissions(user.id, "stoktok")
    assert perms == [
        "stoktok:*",
        "stoktok:news:read",
        "stoktok:portfolio:read",
        "stoktok:portfolio:write",
    ]


async def test_effective_permissions_super_admin_seed(db_session: AsyncSession) -> None:
    role = await db_session.scalar(select(Role).where(Role.key == "admin:super-admin"))
    assert role is not None
    user = await make_user(db_session)
    await assign(db_session, user, role)
    authz = AuthzService(db_session)
    assert await authz.effective_roles(user.id, "admin") == ["admin:super-admin"]
    assert await authz.effective_permissions(user.id, "admin") == ["admin:*"]


async def test_effective_permissions_drop_foreign_prefix(db_session: AsyncSession) -> None:
    app = await make_app(db_session)
    user = await make_user(db_session)
    # The CHECK allows it; the "prefix == app key" rule comes with the USR-02b endpoints.
    bad = await make_permission(db_session, app, "admin:*", is_admin=True)
    good = await make_permission(db_session, app, "stoktok:portfolio:read")
    role = await make_role(db_session, app, "stoktok:member")
    await grant(db_session, role, bad, good)
    await assign(db_session, user, role)
    authz = AuthzService(db_session)
    assert await authz.effective_permissions(user.id, "stoktok") == ["stoktok:portfolio:read"]
    assert await authz.effective_permissions(user.id, "admin") == []


@pytest.mark.parametrize(
    ("granted", "required", "expected"),
    [
        ({"a:x:read"}, "a:x:read", True),
        ({"a:*"}, "a:x:read", True),
        ({"a:*"}, "a:new:thing", True),
        ({"a:*"}, "b:x:read", False),
        ({"*"}, "a:x:read", False),
        ({"a:x:read"}, "a:x:write", False),
        ({"a:*"}, "a:*", True),
        ({"a:*"}, "nocolon", False),
        (set(), "a:x:read", False),
    ],
)
def test_permits(granted: Collection[str], required: str, expected: bool) -> None:
    assert permits(granted, required) is expected
