import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.join_requests.models import AppJoinRequest, JoinRequestStatus
from app.modules.users.models import User


class JoinRequestRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_pending(self, app_id: uuid.UUID, user_id: uuid.UUID) -> AppJoinRequest | None:
        return await self.session.scalar(
            select(AppJoinRequest).where(
                AppJoinRequest.app_id == app_id,
                AppJoinRequest.user_id == user_id,
                AppJoinRequest.status == JoinRequestStatus.PENDING.value,
            )
        )

    async def rejected_within(self, app_id: uuid.UUID, user_id: uuid.UUID, seconds: int) -> bool:
        """A rejection newer than `seconds`, by the database clock."""
        found = await self.session.scalar(
            select(AppJoinRequest.id)
            .where(
                AppJoinRequest.app_id == app_id,
                AppJoinRequest.user_id == user_id,
                AppJoinRequest.status == JoinRequestStatus.REJECTED.value,
                AppJoinRequest.decided_at > func.now() - timedelta(seconds=seconds),
            )
            .limit(1)
        )
        return found is not None

    async def add(self, row: AppJoinRequest) -> AppJoinRequest:
        """Insert in a savepoint: an `IntegrityError` leaves the outer transaction usable."""
        async with self.session.begin_nested():
            self.session.add(row)
            await self.session.flush()
        await self.session.refresh(row)  # server default (`created_at`)
        return row

    async def get_in_app(self, app_id: uuid.UUID, request_id: uuid.UUID) -> AppJoinRequest | None:
        return await self.session.scalar(
            select(AppJoinRequest).where(
                AppJoinRequest.id == request_id, AppJoinRequest.app_id == app_id
            )
        )

    async def get_in_app_for_update(
        self, app_id: uuid.UUID, request_id: uuid.UUID
    ) -> AppJoinRequest | None:
        return await self.session.scalar(
            select(AppJoinRequest)
            .where(AppJoinRequest.id == request_id, AppJoinRequest.app_id == app_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def page(
        self, app_id: uuid.UUID, status: str, *, limit: int, offset: int
    ) -> tuple[list[tuple[AppJoinRequest, User]], int]:
        where = (AppJoinRequest.app_id == app_id, AppJoinRequest.status == status)
        total = await self.session.scalar(
            select(func.count()).select_from(AppJoinRequest).where(*where)
        )
        rows = await self.session.execute(
            select(AppJoinRequest, User)
            .join(User, User.id == AppJoinRequest.user_id)
            .where(*where)
            .order_by(AppJoinRequest.created_at, AppJoinRequest.id)
            .limit(limit)
            .offset(offset)
        )
        return [(r, u) for r, u in rows], total or 0

    async def get_user(self, user_id: uuid.UUID) -> User | None:
        return await self.session.get(User, user_id)

    async def decide(
        self, row: AppJoinRequest, status: JoinRequestStatus, decided_by: uuid.UUID | None
    ) -> None:
        row.status = status.value
        row.decided_by = decided_by
        row.decided_at = func.now()
        await self.session.flush()
        await self.session.refresh(row)
