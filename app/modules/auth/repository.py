from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import LoginFailure
from app.modules.auth.throttle import COUNTER_RESET


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

    async def record_failure(self, email_hmac: bytes) -> int:
        """Count one failure (restarting at 1 after COUNTER_RESET); returns the new count."""
        stmt = pg_insert(LoginFailure).values(email_hmac=email_hmac, failures=1)
        stale = LoginFailure.last_failed_at < func.now() - COUNTER_RESET
        result = await self.session.execute(
            stmt.on_conflict_do_update(
                index_elements=[LoginFailure.email_hmac],
                set_={
                    "failures": case((stale, 1), else_=LoginFailure.failures + 1),
                    "last_failed_at": func.now(),
                },
            ).returning(LoginFailure.failures)
        )
        return int(result.scalar_one())

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
        """Persist the failure before the 401 is raised (ADR-0004)."""
        await self.session.commit()
