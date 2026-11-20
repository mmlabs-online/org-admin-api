import uuid
from datetime import datetime

from sqlalchemy import func, literal, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.deployer_credentials.models import DeployerCredential


class DeployerCredentialRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def lock(self) -> None:
        """Serialise issue and revoke until the transaction ends (limit is check-then-insert)."""
        await self.session.execute(
            select(
                func.pg_advisory_xact_lock(
                    func.hashtextextended(literal("deployer_credentials"), 0)
                )
            )
        )

    async def add(self, credential: DeployerCredential) -> None:
        self.session.add(credential)
        await self.session.flush()
        await self.session.refresh(credential)  # server defaults (`created_at`)

    async def count_active(self) -> int:
        return (
            await self.session.scalar(
                select(func.count())
                .select_from(DeployerCredential)
                .where(DeployerCredential.revoked_at.is_(None))
            )
            or 0
        )

    async def page(self, *, limit: int, offset: int) -> tuple[list[DeployerCredential], int]:
        total = await self.session.scalar(select(func.count()).select_from(DeployerCredential))
        rows = await self.session.scalars(
            select(DeployerCredential)
            .order_by(DeployerCredential.created_at.desc(), DeployerCredential.id)
            .limit(limit)
            .offset(offset)
        )
        return list(rows), total or 0

    async def get(self, credential_id: uuid.UUID) -> DeployerCredential | None:
        return await self.session.scalar(
            select(DeployerCredential)
            .where(DeployerCredential.id == credential_id)
            .execution_options(populate_existing=True)
        )

    async def revoke(self, credential_id: uuid.UUID) -> datetime | None:
        """Sets `revoked_at` from the database clock; None when it was already revoked."""
        return await self.session.scalar(
            update(DeployerCredential)
            .where(DeployerCredential.id == credential_id, DeployerCredential.revoked_at.is_(None))
            .values(revoked_at=func.now())
            .returning(DeployerCredential.revoked_at)
            .execution_options(synchronize_session=False)
        )

    async def get_by_key_id(self, key_id: str) -> DeployerCredential | None:
        return await self.session.scalar(
            select(DeployerCredential)
            .where(DeployerCredential.key_id == key_id)
            .execution_options(populate_existing=True)
        )

    async def touch_last_used(self, credential_id: uuid.UUID) -> bool:
        """Touches only a non-revoked row; False when it was revoked in the meantime."""
        updated = await self.session.scalar(
            update(DeployerCredential)
            .where(DeployerCredential.id == credential_id, DeployerCredential.revoked_at.is_(None))
            .values(last_used_at=func.now())
            .returning(DeployerCredential.id)
            .execution_options(synchronize_session=False)
        )
        return updated is not None
