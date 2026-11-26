"""The OQ89 "org of the app" predicate (USR-39, ADR-0030 §7), repository level."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.entitlements.models import OrgEntitlement
from app.modules.rbac.models import App
from app.modules.rbac.repository import RbacRepository
from tests.rbac_factories import assign, make_app, make_org, make_role, make_user

PAST = datetime.now(UTC) - timedelta(days=1)


async def _setup(db: AsyncSession) -> tuple[App, App]:
    return await make_app(db, "orgapp"), await make_app(db, "otherapp")


async def _is_org_of_app(db: AsyncSession, org_id: uuid.UUID, app: App) -> bool:
    return await RbacRepository(db).is_org_of_app(org_id, app.id)


async def test_org_of_app_by_unexpired_assignment(db_session: AsyncSession) -> None:
    app, _ = await _setup(db_session)
    org = await make_org(db_session)
    user = await make_user(db_session)
    await assign(db_session, user, await make_role(db_session, app, "orgapp:x"), org_id=org.id)
    assert await _is_org_of_app(db_session, org.id, app)


async def test_org_of_app_by_enabled_entitlement(db_session: AsyncSession) -> None:
    app, _ = await _setup(db_session)
    org = await make_org(db_session)
    db_session.add(OrgEntitlement(org_id=org.id, app_id=app.id, plan="pro", limits={}))
    await db_session.flush()
    assert await _is_org_of_app(db_session, org.id, app)


async def test_org_of_app_false_for_expired_assignment_only(db_session: AsyncSession) -> None:
    app, _ = await _setup(db_session)
    org = await make_org(db_session)
    user = await make_user(db_session)
    role = await make_role(db_session, app, "orgapp:x")
    await assign(db_session, user, role, org_id=org.id, expires_at=PAST)
    assert not await _is_org_of_app(db_session, org.id, app)


async def test_org_of_app_false_for_disabled_entitlement_only(db_session: AsyncSession) -> None:
    app, _ = await _setup(db_session)
    org = await make_org(db_session)
    db_session.add(
        OrgEntitlement(org_id=org.id, app_id=app.id, plan="pro", limits={}, enabled=False)
    )
    await db_session.flush()
    assert not await _is_org_of_app(db_session, org.id, app)


async def test_org_of_app_false_for_other_app_only(db_session: AsyncSession) -> None:
    app, other = await _setup(db_session)
    org = await make_org(db_session)
    user = await make_user(db_session)
    await assign(db_session, user, await make_role(db_session, other, "otherapp:x"), org_id=org.id)
    db_session.add(OrgEntitlement(org_id=org.id, app_id=other.id, plan="pro", limits={}))
    await db_session.flush()
    assert not await _is_org_of_app(db_session, org.id, app)


async def test_org_of_app_false_for_suspended_org(db_session: AsyncSession) -> None:
    app, _ = await _setup(db_session)
    org = await make_org(db_session, status="suspended")
    user = await make_user(db_session)
    await assign(db_session, user, await make_role(db_session, app, "orgapp:x"), org_id=org.id)
    db_session.add(OrgEntitlement(org_id=org.id, app_id=app.id, plan="pro", limits={}))
    await db_session.flush()
    assert not await _is_org_of_app(db_session, org.id, app)


async def test_org_of_app_false_for_unknown_org(db_session: AsyncSession) -> None:
    app, _ = await _setup(db_session)
    assert not await _is_org_of_app(db_session, uuid.uuid4(), app)


async def test_org_of_app_ignores_app_scope_assignments(db_session: AsyncSession) -> None:
    app, _ = await _setup(db_session)
    org = await make_org(db_session)
    user = await make_user(db_session)
    await assign(db_session, user, await make_role(db_session, app, "orgapp:x"))
    assert not await _is_org_of_app(db_session, org.id, app)
