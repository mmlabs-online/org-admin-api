"""Dashboard statistics (ADM-015, spec §5.9). HTTP-free (R1).

The queries run one after another on one session, so the document is approximate, not a snapshot.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.actions import AuditAction
from app.modules.audit.repository import AuditRepository
from app.modules.organizations.models import OrganizationStatus
from app.modules.rbac.models import AppStatus
from app.modules.rbac.repository import RbacRepository
from app.modules.stats.repository import StatsRepository
from app.modules.users.models import UserStatus

# Owner product rules (OQ6, 2026-11-17).
SIGN_UP_MONTHS = 12
STATS_TIME_ZONE = "Asia/Ho_Chi_Minh"
SIGN_IN_WINDOW = timedelta(hours=24)


@dataclass(frozen=True)
class DashboardStatsView:
    generated_at: datetime
    users_total: int
    users_by_status: dict[str, int]
    users_unverified: int
    organizations_by_status: dict[str, int]
    apps_by_status: dict[str, int]
    window_start: datetime
    sign_ins_succeeded: int
    sign_ins_failed: int
    sign_ups_by_month: list[tuple[str, int]]
    members_per_app: list[tuple[str, str, int]]  # (app key, name, member count)


class StatsService:
    def __init__(self, session: AsyncSession) -> None:
        self.stats = StatsRepository(session)
        self.rbac = RbacRepository(session)
        self.audit = AuditRepository(session)

    async def dashboard(self) -> DashboardStatsView:
        now = await self.stats.db_now()
        user_rows = await self.stats.user_counts()
        users_by_status = {s.value: user_rows.get(s.value, (0, 0))[0] for s in UserStatus}
        org_rows = await self.stats.organization_counts()
        apps = await self.rbac.list_app_summaries()
        members = await self.rbac.member_counts()
        since = now - SIGN_IN_WINDOW
        logins = await self.audit.count_actions_since(
            [AuditAction.AUTH_LOGIN_SUCCEEDED.value, AuditAction.AUTH_LOGIN_FAILED.value], since
        )
        return DashboardStatsView(
            generated_at=now,
            users_total=sum(users_by_status.values()),
            users_by_status=users_by_status,
            users_unverified=sum(unverified for _, unverified in user_rows.values()),
            organizations_by_status={s.value: org_rows.get(s.value, 0) for s in OrganizationStatus},
            apps_by_status={s.value: sum(1 for a in apps if a[3] == s.value) for s in AppStatus},
            window_start=since,
            sign_ins_succeeded=logins.get(AuditAction.AUTH_LOGIN_SUCCEEDED.value, 0),
            sign_ins_failed=logins.get(AuditAction.AUTH_LOGIN_FAILED.value, 0),
            sign_ups_by_month=await self.stats.sign_ups_by_month(
                now, SIGN_UP_MONTHS, STATS_TIME_ZONE
            ),
            members_per_app=[(key, name, members.get(app_id, 0)) for app_id, key, name, _ in apps],
        )
