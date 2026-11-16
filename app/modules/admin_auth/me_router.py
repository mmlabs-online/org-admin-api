"""Self-scoped admin reads (R3 class 1, ADR-0008 USR-11 amendment): Bearer `aud=admin`, no
`require_permission`, caller's own data only. Adding a route here needs an ADR-0008 amendment
and an entry in `SELF_SCOPED` (tests/test_route_auth_classes.py)."""

from fastapi import APIRouter, Depends, Response

from app.api.deps import SessionDep
from app.modules.admin_auth.deps import AdminDep, current_admin
from app.modules.admin_auth.schemas import MePermissions
from app.modules.admin_auth.service import AdminAuthService

router = APIRouter(prefix="/me", tags=["me"], dependencies=[Depends(current_admin)])


@router.get(
    "/permissions",
    response_model=MePermissions,
    responses={401: {"description": "Missing or invalid Bearer token"}},
    summary="The caller's own admin roles and effective permissions",
)
async def get_my_permissions(
    session: SessionDep, principal: AdminDep, response: Response
) -> MePermissions:
    response.headers["Cache-Control"] = "no-store"
    view = await AdminAuthService(session).self_view(principal)
    return MePermissions(user_id=view.user_id, roles=view.roles, permissions=view.permissions)
