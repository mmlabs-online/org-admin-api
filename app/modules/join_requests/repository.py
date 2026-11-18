import uuid
from datetime import timedelta
from typing import Any, cast

from sqlalchemy import ColumnElement, CursorResult, Delete, and_, delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.join_requests.models import AppJoinRequest, JoinRequestStatus
from app.modules.rbac.models import App
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

    async def pending_at_least(self, app_id: uuid.UUID, n: int) -> bool:
        """Whether the app has at least `n` pending requests (bounded: stops at the n-th)."""
        found = await self.session.scalar(
            select(AppJoinRequest.id)
            .where(
                AppJoinRequest.app_id == app_id,
                AppJoinRequest.status == JoinRequestStatus.PENDING.value,
            )
            .order_by(AppJoinRequest.created_at, AppJoinRequest.id)
            .offset(n - 1)
            .limit(1)
        )
        return found is not None

    async def cancel_pending_for_user(
        self, user_id: uuid.UUID, decided_by: uuid.UUID | None
    ) -> list[tuple[uuid.UUID, str]]:
        """Cancel every pending request of the user; returns `(request_id, app_key)`, sorted."""
        result = await self.session.execute(
            update(AppJoinRequest)
            .where(
                AppJoinRequest.user_id == user_id,
                AppJoinRequest.status == JoinRequestStatus.PENDING.value,
            )
            .values(
                status=JoinRequestStatus.CANCELLED.value,
                decided_at=func.now(),
                decided_by=decided_by,
            )
            .returning(AppJoinRequest.id, AppJoinRequest.app_id)
            .execution_options(synchronize_session=False)
        )
        cancelled = result.all()
        if not cancelled:
            return []
        found = await self.session.execute(
            select(App.id, App.key).where(App.id.in_({app_id for _, app_id in cancelled}))
        )
        keys = {app_id: key for app_id, key in found}
        return sorted((rid, keys[app_id]) for rid, app_id in cancelled)

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


class JoinRequestRetentionRepository:
    """Batched retention deletes for decided `join_requests` (USR-15). DB clock; 1-day floor."""

    MIN_AGE = timedelta(days=1)

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def set_lock_timeout(self) -> None:
        """Bound any lock wait of the current transaction (SKIP LOCKED avoids most)."""
        await self.session.execute(text("SET LOCAL lock_timeout = '2s'"))

    def _where(self, retention: timedelta, cooldown_seconds: int) -> ColumnElement[bool]:
        if retention < self.MIN_AGE:
            raise ValueError("retention age is below the minimum")
        old = and_(
            AppJoinRequest.status != JoinRequestStatus.PENDING.value,
            AppJoinRequest.decided_at < func.now() - retention,
        )
        if cooldown_seconds <= 0:
            return old
        # A rejection inside the cooldown is what `rejected_within` reads: never purge it.
        in_cooldown = and_(
            AppJoinRequest.status == JoinRequestStatus.REJECTED.value,
            AppJoinRequest.decided_at > func.now() - timedelta(seconds=cooldown_seconds),
        )
        return and_(old, ~in_cooldown)

    async def count(self, retention: timedelta, cooldown_seconds: int) -> int:
        query = select(func.count()).select_from(AppJoinRequest)
        return int(
            await self.session.scalar(query.where(self._where(retention, cooldown_seconds))) or 0
        )

    async def purge(self, retention: timedelta, cooldown_seconds: int, batch: int) -> int:
        """Delete up to `batch` old decided rows, skipping rows another transaction holds."""
        # MATERIALIZED: select and lock the ids once (BUG-001).
        batch_ids = (
            select(AppJoinRequest.id)
            .where(self._where(retention, cooldown_seconds))
            .order_by(AppJoinRequest.decided_at, AppJoinRequest.id)
            .limit(batch)
            .with_for_update(skip_locked=True)
            .cte("batch")
            .prefix_with("MATERIALIZED")
        )
        stmt: Delete = delete(AppJoinRequest).where(AppJoinRequest.id.in_(select(batch_ids.c.id)))
        result = await self.session.execute(stmt.execution_options(synchronize_session=False))
        return int(cast(CursorResult[Any], result).rowcount)

    async def commit(self) -> None:
        """End the batch's transaction (release its row locks)."""
        await self.session.commit()
