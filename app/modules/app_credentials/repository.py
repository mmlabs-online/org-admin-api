import uuid
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.app_credentials.models import AppCredential
from app.modules.rbac.models import App


class AppCredentialRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, credential: AppCredential) -> None:
        self.session.add(credential)
        await self.session.flush()
        await self.session.refresh(credential)  # server defaults (`created_at`)

    async def count_active(self, app_id: uuid.UUID) -> int:
        return (
            await self.session.scalar(
                select(func.count())
                .select_from(AppCredential)
                .where(AppCredential.app_id == app_id, AppCredential.revoked_at.is_(None))
            )
            or 0
        )

    async def page(
        self, app_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[list[AppCredential], int]:
        where = AppCredential.app_id == app_id
        total = await self.session.scalar(
            select(func.count()).select_from(AppCredential).where(where)
        )
        rows = await self.session.scalars(
            select(AppCredential)
            .where(where)
            .order_by(AppCredential.created_at.desc(), AppCredential.id)
            .limit(limit)
            .offset(offset)
        )
        return list(rows), total or 0

    async def get(self, app_id: uuid.UUID, credential_id: uuid.UUID) -> AppCredential | None:
        return await self.session.scalar(
            select(AppCredential)
            .where(AppCredential.id == credential_id, AppCredential.app_id == app_id)
            .execution_options(populate_existing=True)
        )

    async def revoke(self, credential_id: uuid.UUID) -> datetime | None:
        """Sets `revoked_at` from the database clock; None when it was already revoked."""
        return await self.session.scalar(
            update(AppCredential)
            .where(AppCredential.id == credential_id, AppCredential.revoked_at.is_(None))
            .values(revoked_at=func.now())
            .returning(AppCredential.revoked_at)
            .execution_options(synchronize_session=False)
        )

    async def get_with_app(self, key_id: str) -> tuple[AppCredential, App] | None:
        result = await self.session.execute(
            select(AppCredential, App)
            .join(App, App.id == AppCredential.app_id)
            .where(AppCredential.key_id == key_id)
            .execution_options(populate_existing=True)
        )
        row = result.one_or_none()
        return None if row is None else (row[0], row[1])

    async def touch_last_used(self, credential_id: uuid.UUID) -> None:
        await self.session.execute(
            update(AppCredential)
            .where(AppCredential.id == credential_id)
            .values(last_used_at=func.now())
            .execution_options(synchronize_session=False)
        )
