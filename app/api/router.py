from fastapi import APIRouter

from app.modules.auth.router import router as auth_router
from app.modules.bug_reports.auth_router import router as bug_report_auth_router
from app.modules.bug_reports.router import router as bug_report_router
from app.modules.entitlements.router import router as entitlements_router
from app.modules.invitations.router import router as invitations_router
from app.modules.organizations.auth_router import router as org_auth_router
from app.modules.organizations.router import router as organizations_router
from app.modules.rbac.catalog_router import router as catalog_router
from app.modules.stats.router import router as stats_router

api_router = APIRouter()
api_router.include_router(organizations_router)
api_router.include_router(entitlements_router)
api_router.include_router(stats_router)
api_router.include_router(auth_router)
api_router.include_router(org_auth_router)
api_router.include_router(bug_report_auth_router)
api_router.include_router(bug_report_router)
api_router.include_router(invitations_router)
# Public (D17), always mounted; the staff RBAC router is gated in `create_app`.
api_router.include_router(catalog_router)
