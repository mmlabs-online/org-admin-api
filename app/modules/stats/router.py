"""Admin (Bearer) dashboard statistics — R3 class 1, R9 exception D26 (ADM-015)."""

from fastapi import APIRouter, Depends, Response

from app.api.deps import SessionDep
from app.modules.admin_auth.deps import current_admin, require_permission
from app.modules.stats.schemas import (
    AppMembers,
    AppStats,
    AppStatusCounts,
    DashboardStats,
    MonthCount,
    OrganizationStats,
    OrganizationStatusCounts,
    SignIns24h,
    SignUps,
    UserStats,
    UserStatusCounts,
)
from app.modules.stats.service import STATS_TIME_ZONE, StatsService

router = APIRouter(prefix="/stats", tags=["stats"], dependencies=[Depends(current_admin)])

DESCRIPTION = """One read-only document for the dashboard (counts only, no audit rows).

Caveats: failed sign-ins include unknown emails (anonymous actor) but not requests refused with
429 or by Turnstile. Sign-ups include self sign-ups and CLI-created accounts and do not depend on
the audit log or its retention. Sign-ins are event counts over a rolling 24 h window, not distinct
users. Sign-ups cover the last 12 months in the `Asia/Ho_Chi_Minh` time zone; the last month is
partial. Members per app: distinct `active` users with a non-expired role assignment on the app
(app scope or any org). The counts are approximate (not one snapshot). Clients ignore unknown keys.
"""


@router.get(
    "/dashboard",
    response_model=DashboardStats,
    summary="Dashboard statistics",
    description=DESCRIPTION,
    **require_permission("admin:stats:read"),
)
async def get_dashboard(session: SessionDep, response: Response) -> DashboardStats:
    response.headers["Cache-Control"] = "no-store"
    v = await StatsService(session).dashboard()
    return DashboardStats(
        generated_at=v.generated_at,
        users=UserStats(
            total=v.users_total,
            by_status=UserStatusCounts(**v.users_by_status),
            unverified=v.users_unverified,
        ),
        organizations=OrganizationStats(
            total=sum(v.organizations_by_status.values()),
            by_status=OrganizationStatusCounts(**v.organizations_by_status),
        ),
        apps=AppStats(
            total=sum(v.apps_by_status.values()), by_status=AppStatusCounts(**v.apps_by_status)
        ),
        sign_ins_24h=SignIns24h(
            window_start=v.window_start,
            window_end=v.generated_at,
            succeeded=v.sign_ins_succeeded,
            failed=v.sign_ins_failed,
        ),
        sign_ups=SignUps(
            time_zone=STATS_TIME_ZONE,
            by_month=[MonthCount(month=m, count=c) for m, c in v.sign_ups_by_month],
        ),
        members_per_app=[
            AppMembers(app_key=k, name=n, member_count=c) for k, n, c in v.members_per_app
        ],
    )
