from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, case, delete, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import LoginFailure
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
