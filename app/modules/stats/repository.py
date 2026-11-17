from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.organizations.models import Organization
from app.modules.users.models import User

# Months are generated and zero-filled in SQL; the join range is sargable on `created_at`.
# Never bucket with `date_trunc('month', created_at)` alone: the session zone is UTC.
SIGN_UPS_BY_MONTH = text(
    """
    WITH cur AS (SELECT date_trunc('month', CAST(:now AS timestamptz) AT TIME ZONE :tz) AS m),
         months AS (SELECT generate_series(cur.m - make_interval(months => :months - 1), cur.m,
                                           interval '1 month') AS m FROM cur)
    SELECT to_char(months.m, 'YYYY-MM') AS month, count(u.id) AS count
    FROM months LEFT JOIN users u
      ON u.created_at >= (months.m AT TIME ZONE :tz)
     AND u.created_at <  ((months.m + interval '1 month') AT TIME ZONE :tz)
    GROUP BY months.m ORDER BY months.m
    """
)


class StatsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def db_now(self) -> datetime:
        """Transaction time of the database clock."""
        return (await self.session.execute(select(func.now()))).scalar_one()

    async def user_counts(self) -> dict[str, tuple[int, int]]:
        """status -> (count, count with an unverified email)."""
        result = await self.session.execute(
            select(
                User.status,
                func.count(),
                func.count().filter(User.email_verified_at.is_(None)),
            ).group_by(User.status)
        )
        return {status: (total, unverified) for status, total, unverified in result}

    async def organization_counts(self) -> dict[str, int]:
        result = await self.session.execute(
            select(Organization.status, func.count()).group_by(Organization.status)
        )
        return {status: total for status, total in result}

    async def sign_ups_by_month(self, now: datetime, months: int, tz: str) -> list[tuple[str, int]]:
        result = await self.session.execute(
            SIGN_UPS_BY_MONTH, {"now": now, "months": months, "tz": tz}
        )
        return [(month, count) for month, count in result]
