"""`EntitlementRepository.get_active_for` reuses the single ADM-006 `ACTIVE` definition."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.entitlements.models import OrgEntitlement
from app.modules.entitlements.repository import EntitlementRepository
from tests.auth_factories import make_login_app
from tests.rbac_factories import make_org


async def _row(
    db: AsyncSession,
    *,
    enabled: bool = True,
    org_status: str = "active",
    app_status: str = "active",
) -> tuple[EntitlementRepository, OrgEntitlement]:
    org = await make_org(db, status=org_status)
    app = await make_login_app(db, "calechip", org_mode="required", status=app_status)
    ent = OrgEntitlement(
        org_id=org.id, app_id=app.id, plan="pro", limits={"seats": 5}, enabled=enabled
    )
    db.add(ent)
    await db.flush()
    return EntitlementRepository(db), ent


async def test_get_active_for_each_active_leg(db_session: AsyncSession) -> None:
    repo, ent = await _row(db_session)
    view = await repo.get_active_for(ent.org_id, ent.app_id)
    assert view is not None
    assert (view.plan, view.limits, view.active) == ("pro", {"seats": 5}, True)


@pytest.mark.parametrize(
    "flipped", [{"enabled": False}, {"org_status": "suspended"}, {"app_status": "disabled"}]
)
async def test_get_active_for_inactive_legs_return_none(
    db_session: AsyncSession, flipped: dict[str, object]
) -> None:
    repo, ent = await _row(db_session, **flipped)  # type: ignore[arg-type]
    assert await repo.get_active_for(ent.org_id, ent.app_id) is None


async def test_get_active_for_scoped_to_org_and_app(db_session: AsyncSession) -> None:
    repo, ent = await _row(db_session)
    other_org = await make_org(db_session)
    other_app = await make_login_app(db_session, "other", org_mode="required")
    assert await repo.get_active_for(other_org.id, ent.app_id) is None
    assert await repo.get_active_for(ent.org_id, other_app.id) is None
