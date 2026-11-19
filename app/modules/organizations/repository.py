import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import ColumnElement, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.listing import ilike_any, sort_clauses
from app.db.base import bumped_updated_at
from app.modules.entitlements.models import OrgEntitlement
from app.modules.organizations.models import Organization, OrganizationStatus
from app.modules.organizations.schemas import OrganizationSort
from app.modules.rbac.models import Role, RoleAssignment

_SORT_COLUMNS = {
    "created_at": Organization.created_at,
    "name": Organization.name,
    "slug": Organization.slug,
}


class OrganizationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, org_id: uuid.UUID) -> Organization | None:
        return await self.session.get(Organization, org_id)

    async def get_by_slug(self, slug: str) -> Organization | None:
        result = await self.session.execute(select(Organization).where(Organization.slug == slug))
        return result.scalar_one_or_none()

    async def list(
        self,
        *,
        status: OrganizationStatus | None,
        q: str | None,
        sort: OrganizationSort,
        limit: int,
        offset: int,
    ) -> tuple[list[Organization], int]:
        conditions: list[ColumnElement[bool]] = []
        if status is not None:
            conditions.append(Organization.status == status.value)
        if q is not None:
            conditions.append(ilike_any(q, Organization.name, Organization.slug))
        total = await self.session.scalar(
            select(func.count()).select_from(Organization).where(*conditions)
        )
        result = await self.session.execute(
            select(Organization)
            .where(*conditions)
            .order_by(*sort_clauses(sort, _SORT_COLUMNS, Organization.id))
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars()), total or 0

    async def update_if_version(
        self, org_id: uuid.UUID, expected: datetime, values: dict[str, object]
    ) -> bool:
        """One conditional UPDATE: atomic compare-and-write on `updated_at` (ADM-024)."""
        result = await self.session.execute(
            update(Organization)
            .where(Organization.id == org_id, Organization.updated_at == expected)
            .values(**values, updated_at=bumped_updated_at(Organization.updated_at))
            .returning(Organization.id)
            .execution_options(synchronize_session=False)
        )
        return result.scalar_one_or_none() is not None

    async def current_version(self, org_id: uuid.UUID) -> datetime | None:
        return await self.session.scalar(
            select(Organization.updated_at).where(Organization.id == org_id)
        )

    async def add(self, org: Organization) -> Organization:
        self.session.add(org)
        await self.session.flush()
        await self.session.refresh(org)
        return org

    async def delete(self, org: Organization) -> None:
        await self.session.delete(org)
        await self.session.flush()

    async def has_role_assignments(self, org_id: uuid.UUID) -> bool:
        """Any assignment in the org, expired included (the FK is RESTRICT)."""
        stmt = select(exists().where(RoleAssignment.org_id == org_id))
        return bool(await self.session.scalar(stmt))

    async def has_entitlements(self, org_id: uuid.UUID) -> bool:
        """Any entitlement of the org (the FK is RESTRICT)."""
        return bool(
            await self.session.scalar(select(exists().where(OrgEntitlement.org_id == org_id)))
        )

    async def count_created_by(self, user_id: uuid.UUID) -> int:
        stmt = (
            select(func.count()).select_from(Organization).where(Organization.created_by == user_id)
        )
        return await self.session.scalar(stmt) or 0

    async def list_for_user_app(
        self, user_id: uuid.UUID, app_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[Sequence[tuple[Organization, Sequence[str]]], int]:
        """Orgs where the user holds an unexpired role of the app in that org (org-exact).

        Returns (org, sorted role keys) ordered by name, id, and the total number of orgs.
        """
        held = (
            select(RoleAssignment.org_id)
            .join(Role, Role.id == RoleAssignment.role_id)
            .where(
                RoleAssignment.user_id == user_id,
                Role.app_id == app_id,
                RoleAssignment.org_id.is_not(None),
                # Database clock, never the Python one.
                or_(RoleAssignment.expires_at.is_(None), RoleAssignment.expires_at > func.now()),
            )
        )
        total = await self.session.scalar(
            select(func.count()).select_from(Organization).where(Organization.id.in_(held))
        )
        orgs = list(
            await self.session.scalars(
                select(Organization)
                .where(Organization.id.in_(held))
                .order_by(Organization.name, Organization.id)
                .limit(limit)
                .offset(offset)
            )
        )
        roles: dict[uuid.UUID, list[str]] = {o.id: [] for o in orgs}
        if orgs:
            rows = await self.session.execute(
                select(RoleAssignment.org_id, Role.key)
                .join(Role, Role.id == RoleAssignment.role_id)
                .where(
                    RoleAssignment.user_id == user_id,
                    Role.app_id == app_id,
                    RoleAssignment.org_id.in_(roles.keys()),
                    or_(
                        RoleAssignment.expires_at.is_(None),
                        RoleAssignment.expires_at > func.now(),
                    ),
                )
                .order_by(Role.key)
            )
            for org_id, key in rows.all():
                if org_id is not None:
                    roles[org_id].append(key)
        return [(o, roles[o.id]) for o in orgs], total or 0
