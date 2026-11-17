"""USR-06a schema: org FK (RESTRICT), `created_by`, `apps.org_owner_role_id` (CHECK, FK)."""

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.organizations.models import Organization
from app.modules.rbac.models import RoleAssignment
from tests.rbac_factories import assign, make_app, make_org, make_role, make_user


async def test_role_assignments_org_fk_restrict(db_session: AsyncSession) -> None:
    app = await make_app(db_session, org_mode="required")
    role = await make_role(db_session, app, "stoktok:member")
    org = await make_org(db_session)
    await assign(db_session, await make_user(db_session), role, org_id=org.id)
    with pytest.raises(IntegrityError, match="fk_role_assignments_org_id_organizations"):
        async with db_session.begin_nested():
            await db_session.execute(
                text("DELETE FROM organizations WHERE id = :id"), {"id": org.id}
            )


async def test_role_assignment_unknown_org_refused(db_session: AsyncSession) -> None:
    app = await make_app(db_session)
    role = await make_role(db_session, app, "stoktok:member")
    with pytest.raises(IntegrityError, match="fk_role_assignments_org_id_organizations"):
        async with db_session.begin_nested():
            await assign(db_session, await make_user(db_session), role, org_id=uuid.uuid4())


async def test_org_id_null_means_app_scope(db_session: AsyncSession) -> None:
    app = await make_app(db_session)
    role = await make_role(db_session, app, "stoktok:member")
    user = await make_user(db_session)
    ra = await assign(db_session, user, role)
    assert ra.org_id is None
    with pytest.raises(IntegrityError, match="uq_role_assignments_user_role_org"):
        async with db_session.begin_nested():
            await assign(db_session, user, role)  # NULLS NOT DISTINCT: still one app-scope row
    assert (
        await db_session.scalar(select(RoleAssignment).where(RoleAssignment.id == ra.id))
    ) is not None


async def test_created_by_set_null_on_user_delete(db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    org = await make_org(db_session, created_by=user.id)
    await db_session.execute(text("DELETE FROM users WHERE id = :id"), {"id": user.id})
    await db_session.refresh(org)
    assert org.created_by is None
    assert await db_session.get(Organization, org.id) is not None


async def test_org_owner_requires_org_mode_check(db_session: AsyncSession) -> None:
    app = await make_app(db_session)  # org_mode none
    role = await make_role(db_session, app, "stoktok:owner")
    with pytest.raises(IntegrityError, match="ck_apps_org_owner_requires_org_mode"):
        async with db_session.begin_nested():
            app.org_owner_role_id = role.id
            await db_session.flush()


async def test_org_owner_role_same_app_fk(db_session: AsyncSession) -> None:
    app = await make_app(db_session, org_mode="required")
    other = await make_app(db_session, "calechip")
    foreign = await make_role(db_session, other, "calechip:owner")
    with pytest.raises(IntegrityError, match="fk_apps_org_owner_role_same_app"):
        async with db_session.begin_nested():
            app.org_owner_role_id = foreign.id
            await db_session.flush()
