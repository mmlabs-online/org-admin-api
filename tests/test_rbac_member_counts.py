"""RbacRepository.member_counts: the OQ5 member definition shared with ADM-016 (ADM-015)."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.rbac.models import App
from app.modules.rbac.repository import RbacRepository
from tests.admin_auth_factories import make_staff
from tests.auth_factories import make_login_user
from tests.rbac_factories import assign, make_app, make_org, make_role


async def test_app_and_org_scope_counted_once(db_session: AsyncSession) -> None:
    app = await make_app(db_session, "m-app")
    r1, r2 = await make_role(db_session, app, "mm:a"), await make_role(db_session, app, "mm:b")
    o1, o2 = await make_org(db_session), await make_org(db_session)
    user = await make_login_user(db_session)
    await assign(db_session, user, r1)
    await assign(db_session, user, r2)
    await assign(db_session, user, r1, org_id=o1.id)
    await assign(db_session, user, r1, org_id=o2.id)
    assert await RbacRepository(db_session).member_counts([app.id]) == {app.id: 1}


async def test_expired_assignment_excluded(db_session: AsyncSession) -> None:
    app = await make_app(db_session, "m-exp")
    role = await make_role(db_session, app, "mm:r")
    past, future = datetime.now(UTC) - timedelta(days=1), datetime.now(UTC) + timedelta(days=1)
    await assign(db_session, await make_login_user(db_session), role, expires_at=past)
    repo = RbacRepository(db_session)
    assert await repo.member_counts([app.id]) == {}
    await assign(db_session, await make_login_user(db_session), role, expires_at=future)
    assert await repo.member_counts([app.id]) == {app.id: 1}


async def test_disabled_user_excluded_unverified_counted(db_session: AsyncSession) -> None:
    app = await make_app(db_session, "m-status")
    role = await make_role(db_session, app, "mm:r")
    await assign(db_session, await make_login_user(db_session, status="disabled"), role)
    await assign(db_session, await make_login_user(db_session, verified=False), role)
    assert await RbacRepository(db_session).member_counts([app.id]) == {app.id: 1}


async def test_admin_app_counts_staff(db_session: AsyncSession) -> None:
    admin = await db_session.scalar(select(App).where(App.key == "admin"))
    assert admin is not None
    repo = RbacRepository(db_session)
    before = (await repo.member_counts([admin.id])).get(admin.id, 0)
    await make_staff(db_session, "admin:stats:read")
    assert (await repo.member_counts([admin.id]))[admin.id] == before + 1


async def test_app_ids_filter_and_missing_apps(db_session: AsyncSession) -> None:
    a = await make_app(db_session, "m-f0")
    b = await make_app(db_session, "m-f1")
    c = await make_app(db_session, "m-f2")
    for app in (a, c):
        await assign(
            db_session, await make_login_user(db_session), await make_role(db_session, app, "mm:r")
        )
    repo = RbacRepository(db_session)
    assert await repo.member_counts([a.id, b.id]) == {a.id: 1}
    assert await repo.member_counts([]) == {}
    assert {a.id, c.id} <= set(await repo.member_counts())
