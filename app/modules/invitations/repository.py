import uuid
from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.invitations.models import OrgInvitation
from app.modules.invitations.schemas import InvitationStatus
from app.modules.rbac.models import App, Role

# (id, app key, role id, role key, invited_by, created_at, expires_at, accepted_at, accepted_by,
# revoked_at, status) — never the token hash or the email HMAC.
InvitationRow = tuple[
    uuid.UUID,
    str,
    uuid.UUID,
    str,
    uuid.UUID | None,
    datetime,
    datetime,
    datetime | None,
    uuid.UUID | None,
    datetime | None,
    str,
]


class InvitationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def db_now(self) -> datetime:
        return (await self.session.execute(select(func.now()))).scalar_one()

    async def count_created_by_24h(self, user_id: uuid.UUID) -> int:
        count = await self.session.scalar(
            select(func.count()).where(
                OrgInvitation.invited_by == user_id,
                OrgInvitation.created_at > func.now() - timedelta(hours=24),
            )
        )
        return int(count or 0)

    async def count_pending_for_org(self, org_id: uuid.UUID) -> int:
        count = await self.session.scalar(
            select(func.count()).where(
                OrgInvitation.org_id == org_id,
                OrgInvitation.accepted_at.is_(None),
                OrgInvitation.revoked_at.is_(None),
                OrgInvitation.expires_at > func.now(),
            )
        )
        return int(count or 0)

    async def supersede_pending(
        self, org_id: uuid.UUID, app_id: uuid.UUID, email_hmac: bytes
    ) -> list[uuid.UUID]:
        """Revoke the pending invitations of this email in this org and app; their ids."""
        result = await self.session.scalars(
            update(OrgInvitation)
            .where(
                OrgInvitation.org_id == org_id,
                OrgInvitation.app_id == app_id,
                OrgInvitation.email_hmac == email_hmac,
                OrgInvitation.accepted_at.is_(None),
                OrgInvitation.revoked_at.is_(None),
            )
            .values(revoked_at=func.now())
            .returning(OrgInvitation.id)
        )
        return list(result)

    async def add(
        self,
        *,
        org_id: uuid.UUID,
        app_id: uuid.UUID,
        role_id: uuid.UUID,
        email_hmac: bytes,
        token_hash: bytes,
        invited_by: uuid.UUID,
        ttl: timedelta,
    ) -> OrgInvitation:
        """`expires_at` is on the database clock, like every other expiry check.

        The partial unique index `uq_org_invitations_pending` cannot race here: the caller holds
        the app lock (`lock_app`) and the per-email advisory lock, which serialize every create.
        If that lock is ever removed, map its IntegrityError to ConflictError."""
        invitation = OrgInvitation(
            org_id=org_id,
            app_id=app_id,
            role_id=role_id,
            email_hmac=email_hmac,
            token_hash=token_hash,
            invited_by=invited_by,
            expires_at=await self.db_now() + ttl,
        )
        self.session.add(invitation)
        await self.session.flush()
        return invitation

    async def get_pending_for_update(
        self, token_hash: bytes, app_id: uuid.UUID
    ) -> OrgInvitation | None:
        return await self.session.scalar(
            select(OrgInvitation)
            .where(
                OrgInvitation.token_hash == token_hash,
                OrgInvitation.app_id == app_id,
                OrgInvitation.accepted_at.is_(None),
                OrgInvitation.revoked_at.is_(None),
                OrgInvitation.expires_at > func.now(),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def consume(self, invitation_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        """Conditional UPDATE: False when another request consumed or revoked it first."""
        result = await self.session.scalar(
            update(OrgInvitation)
            .where(
                OrgInvitation.id == invitation_id,
                OrgInvitation.accepted_at.is_(None),
                OrgInvitation.revoked_at.is_(None),
                OrgInvitation.expires_at > func.now(),
            )
            .values(accepted_at=func.now(), accepted_by=user_id)
            .returning(OrgInvitation.id)
        )
        return result is not None

    @staticmethod
    def status_expr() -> ColumnElement[str]:
        """The single definition of an invitation's status, on the database clock."""
        return case(
            (OrgInvitation.accepted_at.is_not(None), InvitationStatus.ACCEPTED.value),
            (OrgInvitation.revoked_at.is_not(None), InvitationStatus.REVOKED.value),
            (OrgInvitation.expires_at <= func.now(), InvitationStatus.EXPIRED.value),
            else_=InvitationStatus.PENDING.value,
        )

    async def page_for_org(
        self, org_id: uuid.UUID, *, status: InvitationStatus | None, limit: int, offset: int
    ) -> tuple[list[InvitationRow], int]:
        filters: list[ColumnElement[bool]] = [OrgInvitation.org_id == org_id]
        if status is not None:
            filters.append(self.status_expr() == status.value)
        total = await self.session.scalar(
            select(func.count()).select_from(OrgInvitation).where(*filters)
        )
        result = await self.session.execute(
            select(
                OrgInvitation.id,
                App.key,
                OrgInvitation.role_id,
                Role.key,
                OrgInvitation.invited_by,
                OrgInvitation.created_at,
                OrgInvitation.expires_at,
                OrgInvitation.accepted_at,
                OrgInvitation.accepted_by,
                OrgInvitation.revoked_at,
                self.status_expr().label("status"),
            )
            .join(App, App.id == OrgInvitation.app_id)
            .join(
                Role,
                (Role.id == OrgInvitation.role_id) & (Role.app_id == OrgInvitation.app_id),
            )
            .where(*filters)
            .order_by(OrgInvitation.created_at.desc(), OrgInvitation.id)
            .limit(limit)
            .offset(offset)
        )
        return [(*r,) for r in result.all()], total or 0

    async def revoke_for_org(self, org_id: uuid.UUID, invitation_id: uuid.UUID) -> uuid.UUID | None:
        """Conditional UPDATE (atomic against `consume`): the app id when this call revoked it."""
        return await self.session.scalar(
            update(OrgInvitation)
            .where(
                OrgInvitation.id == invitation_id,
                OrgInvitation.org_id == org_id,
                OrgInvitation.accepted_at.is_(None),
                OrgInvitation.revoked_at.is_(None),
            )
            .values(revoked_at=func.now())
            .returning(OrgInvitation.app_id)
        )

    async def get_state(
        self, org_id: uuid.UUID, invitation_id: uuid.UUID
    ) -> tuple[datetime | None, datetime | None] | None:
        """`(accepted_at, revoked_at)` of an invitation of this org, or None."""
        row = (
            await self.session.execute(
                select(OrgInvitation.accepted_at, OrgInvitation.revoked_at).where(
                    OrgInvitation.id == invitation_id, OrgInvitation.org_id == org_id
                )
            )
        ).first()
        return None if row is None else (row[0], row[1])

    async def app_key(self, app_id: uuid.UUID) -> str | None:
        return await self.session.scalar(select(App.key).where(App.id == app_id))
