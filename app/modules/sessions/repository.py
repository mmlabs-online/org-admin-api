import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.sessions.models import AccountSession, DeviceSession
from app.modules.users.models import User, UserStatus

GRACE = timedelta(seconds=60)  # D6: previous cookie value stays valid this long
TOUCH_EVERY = timedelta(hours=1)  # `last_seen_at` is refreshed at most this often


class SessionRepository:
    """All times come from the database clock (`now()`)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find_device(self, token_hash: bytes) -> tuple[DeviceSession, datetime] | None:
        """The device whose current or previous hash matches, with the database `now()`."""
        row = (
            await self.session.execute(
                select(DeviceSession, func.now())
                .where(
                    or_(
                        DeviceSession.token_hash == token_hash,
                        DeviceSession.prev_token_hash == token_hash,
                    )
                )
                .limit(1)
                .execution_options(populate_existing=True)
            )
        ).first()
        return None if row is None else (row[0], row[1])

    async def add_device(
        self, token_hash: bytes, *, ip: str | None, user_agent: str | None
    ) -> uuid.UUID:
        device = DeviceSession(token_hash=token_hash, ip=ip, user_agent=user_agent)
        self.session.add(device)
        await self.session.flush()
        return device.id

    async def rotate(self, device_id: uuid.UUID, old_hash: bytes, new_hash: bytes) -> bool:
        """Compare-and-swap on the current hash; False when a concurrent request rotated first."""
        rotated = await self.session.scalar(
            update(DeviceSession)
            .where(
                DeviceSession.id == device_id,
                DeviceSession.token_hash == old_hash,
                DeviceSession.revoked_at.is_(None),
            )
            .values(
                prev_token_hash=DeviceSession.token_hash,
                prev_valid_until=func.now() + GRACE,
                token_hash=new_hash,
                rotated_at=func.now(),
                last_seen_at=func.now(),
            )
            .returning(DeviceSession.id)
            .execution_options(synchronize_session=False)
        )
        return rotated is not None

    async def touch(self, device_id: uuid.UUID) -> None:
        await self.session.execute(
            update(DeviceSession)
            .where(
                DeviceSession.id == device_id,
                DeviceSession.last_seen_at < func.now() - TOUCH_EVERY,
            )
            .values(last_seen_at=func.now())
            .execution_options(synchronize_session=False)
        )

    async def revoke_device(self, device_id: uuid.UUID) -> int | None:
        """Revoke the device session and every active account session on it.

        Returns the number of account sessions revoked, or None when the device was already
        revoked (nothing changed).
        """
        accounts = await self.session.execute(
            update(AccountSession)
            .where(
                AccountSession.device_session_id == device_id,
                AccountSession.revoked_at.is_(None),
            )
            .values(revoked_at=func.now())
            .execution_options(synchronize_session=False)
        )
        device = await self.session.execute(
            update(DeviceSession)
            .where(DeviceSession.id == device_id, DeviceSession.revoked_at.is_(None))
            .values(revoked_at=func.now())
            .execution_options(synchronize_session=False)
        )
        if device.rowcount == 0:  # type: ignore[attr-defined]
            return None
        return int(accounts.rowcount)  # type: ignore[attr-defined]

    async def revoke_all_accounts_of_user(self, user_id: uuid.UUID) -> int:
        """Revoke every active account session of the user, on every device (password reset);
        returns how many were revoked.

        Device sessions stay: they may carry other users' accounts.
        """
        result = await self.session.execute(
            update(AccountSession)
            .where(AccountSession.user_id == user_id, AccountSession.revoked_at.is_(None))
            .values(revoked_at=func.now())
            .execution_options(synchronize_session=False)
        )
        return int(result.rowcount)  # type: ignore[attr-defined]

    async def list_active_for_user(
        self, user_id: uuid.UUID, *, idle: timedelta, max_age: timedelta, limit: int, offset: int
    ) -> tuple[list[tuple[uuid.UUID, datetime, datetime, datetime, Any, str | None]], int]:
        """Account sessions that can still mint a token (same predicates as the device
        session's usability check), newest first, with the device's last seen, IP and UA."""
        where = (
            AccountSession.user_id == user_id,
            AccountSession.revoked_at.is_(None),
            DeviceSession.revoked_at.is_(None),
            DeviceSession.last_seen_at >= func.now() - idle,
            DeviceSession.created_at >= func.now() - max_age,
        )
        join = AccountSession.device_session_id == DeviceSession.id
        rows = await self.session.execute(
            select(
                AccountSession.id,
                AccountSession.created_at,
                AccountSession.last_used_at,
                DeviceSession.last_seen_at,
                DeviceSession.ip,
                DeviceSession.user_agent,
            )
            .join(DeviceSession, join)
            .where(*where)
            .order_by(AccountSession.created_at.desc(), AccountSession.id.desc())
            .limit(limit)
            .offset(offset)
        )
        total = await self.session.scalar(
            select(func.count()).select_from(AccountSession).join(DeviceSession, join).where(*where)
        )
        return [(r[0], r[1], r[2], r[3], r[4], r[5]) for r in rows.all()], total or 0

    async def revoke_account_of_user(self, user_id: uuid.UUID, account_id: uuid.UUID) -> bool:
        """Revoke one account session of the user; the device session is untouched."""
        revoked = await self.session.scalar(
            update(AccountSession)
            .where(
                AccountSession.id == account_id,
                AccountSession.user_id == user_id,
                AccountSession.revoked_at.is_(None),
            )
            .values(revoked_at=func.now())
            .returning(AccountSession.id)
            .execution_options(synchronize_session=False)
        )
        return revoked is not None

    async def revoke_all_accounts_of_user_ids(self, user_id: uuid.UUID) -> list[uuid.UUID]:
        """As `revoke_all_accounts_of_user`, returning the ids that were revoked."""
        result = await self.session.scalars(
            update(AccountSession)
            .where(AccountSession.user_id == user_id, AccountSession.revoked_at.is_(None))
            .values(revoked_at=func.now())
            .returning(AccountSession.id)
            .execution_options(synchronize_session=False)
        )
        return list(result.all())

    async def count_active_accounts(self, device_id: uuid.UUID) -> int:
        count = await self.session.scalar(
            select(func.count())
            .select_from(AccountSession)
            .where(
                AccountSession.device_session_id == device_id,
                AccountSession.revoked_at.is_(None),
            )
        )
        return count or 0

    async def upsert_account(self, device_id: uuid.UUID, user_id: uuid.UUID) -> uuid.UUID:
        """The active account session of `user_id` on the device, created if missing."""
        result = await self.session.execute(
            pg_insert(AccountSession)
            .values(id=uuid.uuid4(), device_session_id=device_id, user_id=user_id)
            .on_conflict_do_update(
                index_elements=[AccountSession.device_session_id, AccountSession.user_id],
                index_where=AccountSession.revoked_at.is_(None),
                set_={"last_used_at": func.now()},
            )
            .returning(AccountSession.id)
        )
        return result.scalar_one()

    async def get_active_account(
        self,
        device_id: uuid.UUID,
        account_id: uuid.UUID,
        *,
        created_before: datetime | None,
    ) -> AccountSession | None:
        """An active account session on the device; with `created_before`, only older ones."""
        query = select(AccountSession).where(
            AccountSession.id == account_id,
            AccountSession.device_session_id == device_id,
            AccountSession.revoked_at.is_(None),
        )
        if created_before is not None:
            query = query.where(AccountSession.created_at < created_before)
        return await self.session.scalar(query.execution_options(populate_existing=True))

    async def is_account_active(self, account_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        """The user's account session and its device are both not revoked (database clock)."""
        query = select(
            exists().where(
                AccountSession.id == account_id,
                AccountSession.user_id == user_id,
                AccountSession.revoked_at.is_(None),
                DeviceSession.id == AccountSession.device_session_id,
                DeviceSession.revoked_at.is_(None),
            )
        )
        return bool(await self.session.scalar(query))

    async def list_active_accounts(
        self, device_id: uuid.UUID, *, created_before: datetime | None
    ) -> list[tuple[AccountSession, User]]:
        """Active account sessions of active users on the device, oldest first (N1 as above)."""
        query = (
            select(AccountSession, User)
            .join(User, User.id == AccountSession.user_id)
            .where(
                AccountSession.device_session_id == device_id,
                AccountSession.revoked_at.is_(None),
                User.status == UserStatus.ACTIVE.value,
            )
            .order_by(AccountSession.created_at, AccountSession.id)
        )
        if created_before is not None:
            query = query.where(AccountSession.created_at < created_before)
        rows = await self.session.execute(query)
        return [(row[0], row[1]) for row in rows.all()]

    async def revoke_account(
        self, device_id: uuid.UUID, account_id: uuid.UUID, *, created_before: datetime | None
    ) -> uuid.UUID | None:
        """With `created_before`, only an account session created strictly before it (N1).

        Returns the user id of the revoked account session, or None when nothing matched.
        """
        query = update(AccountSession).where(
            AccountSession.id == account_id,
            AccountSession.device_session_id == device_id,
            AccountSession.revoked_at.is_(None),
        )
        if created_before is not None:
            query = query.where(AccountSession.created_at < created_before)
        user_id: uuid.UUID | None = await self.session.scalar(
            query.values(revoked_at=func.now())
            .returning(AccountSession.user_id)
            .execution_options(synchronize_session=False)
        )
        return user_id

    async def commit(self) -> None:
        """Persist a write that must survive the error raised right after (ADR-0004)."""
        await self.session.commit()
