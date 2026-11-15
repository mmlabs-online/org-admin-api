"""Public role catalog — R3 class 3 (D17). Never add admin data here.

Public by design: never add `current_admin` or `require_permission` here (ADR-0008); the
route-classification test allow-lists it.

Always mounted (not behind `admin_rbac_api_enabled`); per-IP rate limit `role_catalog`.
"""

from fastapi import APIRouter, Request, Response

from app.api.deps import SessionDep
from app.core.etag import if_none_match, strong_etag
from app.core.rate_limit import rate_limit
from app.modules.rbac.schemas import RoleCatalog
from app.modules.rbac.service import RoleCatalogService

router = APIRouter(prefix="/apps", tags=["role-catalog"])


@router.get(
    "/{key}/roles",
    response_model=RoleCatalog,
    dependencies=[rate_limit("role_catalog")],
    responses={
        304: {"description": "Not modified"},
        404: {"description": "Unknown, disabled or `admin` app"},
        429: {"description": "Rate limited"},
    },
    summary="Public role catalog (D17)",
)
async def get_role_catalog(session: SessionDep, request: Request, key: str) -> Response:
    catalog = await RoleCatalogService(session).read_catalog(key)
    body = catalog.model_dump_json().encode()
    etag = strong_etag(body)
    headers = {"ETag": etag, "Cache-Control": "no-cache"}
    if if_none_match(request.headers.getlist("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)
