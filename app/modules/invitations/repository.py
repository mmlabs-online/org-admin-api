import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.invitations.models import OrgInvitation


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
        """`expires_at` is on the database clock, like every other expiry check."""
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
