import uuid
from collections.abc import Sequence

from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.entitlements.models import OrgEntitlement
from app.modules.organizations.models import Organization
from app.modules.rbac.models import Role, RoleAssignment


class OrganizationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, org_id: uuid.UUID) -> Organization | None:
        return await self.session.get(Organization, org_id)

    async def get_by_slug(self, slug: str) -> Organization | None:
        result = await self.session.execute(select(Organization).where(Organization.slug == slug))
        return result.scalar_one_or_none()

    async def list(self, *, limit: int, offset: int) -> tuple[list[Organization], int]:
        total = await self.session.scalar(select(func.count()).select_from(Organization))
        result = await self.session.execute(
            select(Organization)
            .order_by(Organization.created_at.desc(), Organization.id)
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars()), total or 0

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
