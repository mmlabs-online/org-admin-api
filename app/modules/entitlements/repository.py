import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, and_, func, not_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.entitlements.models import OrgEntitlement
from app.modules.organizations.models import Organization
from app.modules.rbac.models import App

# The single definition of "active" (list filter and per-item field).
ACTIVE: ColumnElement[bool] = and_(
    OrgEntitlement.enabled.is_(True), Organization.status == "active", App.status == "active"
)


@dataclass(frozen=True)
class EntitlementView:
    """Plain values for the router, so it never touches the ORM."""

    id: uuid.UUID
    org_id: uuid.UUID
    app_key: str
    plan: str
    limits: dict[str, int]
    enabled: bool
    active: bool
    created_at: datetime
    updated_at: datetime


def _select() -> Any:  # the stubs reject every `Select[...]` annotation of a 3-column select
    return (
        select(OrgEntitlement, App.key, ACTIVE.label("active"))
        .join(App, App.id == OrgEntitlement.app_id)
        .join(Organization, Organization.id == OrgEntitlement.org_id)
    )


def _view(row: Any) -> EntitlementView:
    ent, app_key, active = row
    return EntitlementView(
        id=ent.id,
        org_id=ent.org_id,
        app_key=app_key,
        plan=ent.plan,
        limits=dict(ent.limits),
        enabled=ent.enabled,
        active=bool(active),
        created_at=ent.created_at,
        updated_at=ent.updated_at,
    )


class EntitlementRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def page(
        self, org_id: uuid.UUID, *, active: bool | None, limit: int, offset: int
    ) -> tuple[list[EntitlementView], int]:
        where: list[ColumnElement[bool]] = [OrgEntitlement.org_id == org_id]
        if active is not None:
            where.append(ACTIVE if active else not_(ACTIVE))
        total = await self.session.scalar(
            select(func.count())
            .select_from(OrgEntitlement)
            .join(App, App.id == OrgEntitlement.app_id)
            .join(Organization, Organization.id == OrgEntitlement.org_id)
            .where(*where)
        )
        result = await self.session.execute(
            _select()
            .where(*where)
            .order_by(App.key, OrgEntitlement.id)
            .limit(limit)
            .offset(offset)
            .execution_options(populate_existing=True)
        )
        return [_view(r) for r in result.all()], total or 0

    async def get_view(self, org_id: uuid.UUID, ent_id: uuid.UUID) -> EntitlementView | None:
        result = await self.session.execute(
            _select()
            .where(OrgEntitlement.org_id == org_id, OrgEntitlement.id == ent_id)
            .execution_options(populate_existing=True)
        )
        row = result.one_or_none()
        return None if row is None else _view(row)

    async def get_for_org(self, org_id: uuid.UUID, ent_id: uuid.UUID) -> OrgEntitlement | None:
        return await self.session.scalar(
            select(OrgEntitlement)
            .where(OrgEntitlement.org_id == org_id, OrgEntitlement.id == ent_id)
            .execution_options(populate_existing=True)
        )

    async def find(self, org_id: uuid.UUID, app_id: uuid.UUID) -> OrgEntitlement | None:
        return await self.session.scalar(
            select(OrgEntitlement).where(
                OrgEntitlement.org_id == org_id, OrgEntitlement.app_id == app_id
            )
        )

    async def add(self, ent: OrgEntitlement) -> None:
        self.session.add(ent)
        await self.session.flush()
        await self.session.refresh(ent)  # server defaults

    async def delete(self, ent: OrgEntitlement) -> None:
        await self.session.delete(ent)
        await self.session.flush()
