import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import (
    ColumnElement,
    CursorResult,
    Delete,
    and_,
    case,
    delete,
    func,
    insert,
    literal,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import EmailSendLog, EmailToken, LoginFailure
from app.modules.auth.throttle import COUNTER_RESET, TURNSTILE_AFTER


@dataclass(frozen=True)
class FailureState:
    failures: int  # 0 when absent or older than COUNTER_RESET
    blocked_until: datetime | None
    now: datetime  # database clock


class LoginFailureRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, email_hmac: bytes) -> FailureState:
        row = (
            await self.session.execute(
                select(
                    LoginFailure.failures,
                    LoginFailure.last_failed_at,
                    LoginFailure.blocked_until,
                    func.now(),
                ).where(LoginFailure.email_hmac == email_hmac)
            )
        ).first()
        if row is None:
            now = (await self.session.execute(select(func.now()))).scalar_one()
            return FailureState(0, None, now)
        failures, last_failed_at, blocked_until, now = row
        if last_failed_at < now - COUNTER_RESET:
            failures = 0
        return FailureState(failures, blocked_until, now)

    async def reserve(self, email_hmac: bytes, *, turnstile_ok: bool) -> int | None:
        """Count one attempt atomically (S1); the new count, or None when refused.

        The upsert takes the row lock and re-checks the block and the Turnstile threshold
        against the latest committed row, so concurrent attempts cannot all pass phase 1.
        A stale counter (older than COUNTER_RESET) restarts at 1.
        """
        stale = LoginFailure.last_failed_at < func.now() - COUNTER_RESET
        allowed = or_(
            stale,
            and_(
                or_(LoginFailure.blocked_until.is_(None), LoginFailure.blocked_until <= func.now()),
                or_(LoginFailure.failures < TURNSTILE_AFTER, literal(turnstile_ok)),
            ),
        )
        stmt = pg_insert(LoginFailure).values(email_hmac=email_hmac, failures=1)
        result = await self.session.execute(
            stmt.on_conflict_do_update(
                index_elements=[LoginFailure.email_hmac],
                set_={
                    "failures": case((stale, 1), else_=LoginFailure.failures + 1),
                    "last_failed_at": func.now(),
                },
                where=allowed,
            ).returning(LoginFailure.failures)
        )
        count = result.scalar_one_or_none()
        return None if count is None else int(count)

    async def set_block(self, email_hmac: bytes, minutes: int) -> None:
        await self.session.execute(
            update(LoginFailure)
            .where(LoginFailure.email_hmac == email_hmac)
            .values(blocked_until=func.now() + timedelta(minutes=minutes))
            .execution_options(synchronize_session=False)
        )

    async def delete(self, email_hmac: bytes) -> None:
        await self.session.execute(
            delete(LoginFailure)
            .where(LoginFailure.email_hmac == email_hmac)
            .execution_options(synchronize_session=False)
        )

    async def commit(self) -> None:
        """Persist the reservation (and release the row lock) before the password check."""
        await self.session.commit()


class EmailTokenRepository:
    """One-time email tokens; times from the database clock."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def invalidate(self, user_id: uuid.UUID, purpose: str) -> None:
        """Mark every unused token of the user for `purpose` as used (superseded)."""
        await self.session.execute(
            update(EmailToken)
            .where(
                EmailToken.user_id == user_id,
                EmailToken.purpose == purpose,
                EmailToken.used_at.is_(None),
            )
            .values(used_at=func.now())
            .execution_options(synchronize_session=False)
        )

    async def issue(
        self, user_id: uuid.UUID, purpose: str, token_hash: bytes, ttl: timedelta
    ) -> None:
        """Supersede older unused tokens, then store the new one."""
        await self.invalidate(user_id, purpose)
        await self.session.execute(
            insert(EmailToken).values(
                id=uuid.uuid4(),
                user_id=user_id,
                purpose=purpose,
                token_hash=token_hash,
                expires_at=func.now() + ttl,
            )
        )

    async def consume(self, token_hash: bytes, purpose: str) -> uuid.UUID | None:
        """Atomically mark an unused, unexpired token as used; its user id, else None."""
        user_id: uuid.UUID | None = await self.session.scalar(
            update(EmailToken)
            .where(
                EmailToken.token_hash == token_hash,
                EmailToken.purpose == purpose,
                EmailToken.used_at.is_(None),
                EmailToken.expires_at > func.now(),
            )
            .values(used_at=func.now())
            .returning(EmailToken.user_id)
            .execution_options(synchronize_session=False)
        )
        return user_id

    async def find_active_user_id(self, token_hash: bytes, purpose: str) -> uuid.UUID | None:
        """The user of an unused, unexpired token, without consuming it."""
        user_id: uuid.UUID | None = await self.session.scalar(
            select(EmailToken.user_id).where(
                EmailToken.token_hash == token_hash,
                EmailToken.purpose == purpose,
                EmailToken.used_at.is_(None),
                EmailToken.expires_at > func.now(),
            )
        )
        return user_id

    async def count_issued_24h(self) -> tuple[int, int]:
        """(all, verify) tokens issued in the last 24 h, i.e. emails actually queued."""
        row = (
            await self.session.execute(
                select(
                    func.count(),
                    func.count().filter(EmailToken.purpose == "verify"),
                ).where(EmailToken.created_at > func.now() - timedelta(hours=24))
            )
        ).one()
        return int(row[0]), int(row[1])

    async def find_user_id(self, token_hash: bytes, purpose: str) -> uuid.UUID | None:
        """The token's user in any state (used, expired)."""
        user_id: uuid.UUID | None = await self.session.scalar(
            select(EmailToken.user_id).where(
                EmailToken.token_hash == token_hash, EmailToken.purpose == purpose
            )
        )
        return user_id


@dataclass(frozen=True)
class SendWindow:
    now: datetime  # database clock
    last: datetime | None
    count: int
    oldest: datetime | None


class EmailSendLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def lock(self, email_hmac: bytes, purpose: str) -> None:
        """Serialize sends for one email and purpose until the transaction ends."""
        await self.session.execute(
            select(
                func.pg_advisory_xact_lock(
                    func.hashtextextended(literal(purpose) + func.encode(email_hmac, "hex"), 0)
                )
            )
        )

    async def window(self, email_hmac: bytes, purpose: str) -> SendWindow:
        row = (
            await self.session.execute(
                select(
                    func.now(),
                    func.max(EmailSendLog.sent_at),
                    func.count(),
                    func.min(EmailSendLog.sent_at),
                ).where(
                    EmailSendLog.email_hmac == email_hmac,
                    EmailSendLog.purpose == purpose,
                    EmailSendLog.sent_at > func.now() - timedelta(hours=24),
                )
            )
        ).one()
        now, last, count, oldest = row
        return SendWindow(now, last, int(count), oldest)

    async def lock_global(self) -> None:
        """Serialize the global daily-cap check across all emails until the transaction ends."""
        await self.session.execute(
            select(func.pg_advisory_xact_lock(func.hashtextextended("email_daily_cap", 0)))
        )

    async def insert(self, email_hmac: bytes, purpose: str) -> None:
        await self.session.execute(
            insert(EmailSendLog).values(id=uuid.uuid4(), email_hmac=email_hmac, purpose=purpose)
        )


class EmailRetentionRepository:
    """Batched retention deletes for `email_send_log` and `email_tokens` (USR-04d).

    Ages are measured on the database clock. Each age has a 24 h floor: the per-email window
    counts send-log rows and the global daily cap counts tokens of the last 24 h.
    """

    SEND_LOG_MIN_AGE = timedelta(hours=24)
    TOKEN_MIN_AGE = timedelta(hours=24)

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def set_lock_timeout(self) -> None:
        """Bound any lock wait of the current transaction (SKIP LOCKED avoids most)."""
        await self.session.execute(text("SET LOCAL lock_timeout = '2s'"))

    @staticmethod
    def _check(older_than: timedelta, floor: timedelta) -> None:
        if older_than < floor:
            raise ValueError("retention age is below the minimum")

    def _send_log_where(self, older_than: timedelta) -> ColumnElement[bool]:
        self._check(older_than, self.SEND_LOG_MIN_AGE)
        return EmailSendLog.sent_at < func.now() - older_than

    def _token_where(self, older_than: timedelta) -> ColumnElement[bool]:
        self._check(older_than, self.TOKEN_MIN_AGE)
        return and_(
            EmailToken.created_at < func.now() - older_than,
            or_(EmailToken.used_at.is_not(None), EmailToken.expires_at <= func.now()),
        )

    async def count_send_log(self, older_than: timedelta) -> int:
        query = select(func.count()).select_from(EmailSendLog)
        return int(await self.session.scalar(query.where(self._send_log_where(older_than))) or 0)

    async def count_tokens(self, older_than: timedelta) -> int:
        query = select(func.count()).select_from(EmailToken)
        return int(await self.session.scalar(query.where(self._token_where(older_than))) or 0)

    async def purge_send_log(self, older_than: timedelta, batch: int) -> int:
        """Delete up to `batch` old rows, skipping rows another transaction holds."""
        ids = (
            select(EmailSendLog.id)
            .where(self._send_log_where(older_than))
            .order_by(EmailSendLog.sent_at)
            .limit(batch)
            .with_for_update(skip_locked=True)
            .scalar_subquery()
        )
        return await self._delete(delete(EmailSendLog).where(EmailSendLog.id.in_(ids)))

    async def purge_tokens(self, older_than: timedelta, batch: int) -> int:
        """Delete up to `batch` old used or expired tokens, skipping locked rows."""
        ids = (
            select(EmailToken.id)
            .where(self._token_where(older_than))
            .order_by(EmailToken.created_at)
            .limit(batch)
            .with_for_update(skip_locked=True)
            .scalar_subquery()
        )
        return await self._delete(delete(EmailToken).where(EmailToken.id.in_(ids)))

    async def _delete(self, stmt: Delete) -> int:
        result = await self.session.execute(stmt.execution_options(synchronize_session=False))
        return int(cast(CursorResult[Any], result).rowcount)

    async def commit(self) -> None:
        """End the batch's transaction (release its row locks)."""
        await self.session.commit()
