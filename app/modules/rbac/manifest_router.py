"""Manifest push — R3 class 4 (ADR-0016): app credential auth, never `current_admin`.

No CORS (a server-to-server call), per-IP rate limit before authentication.
"""

from fastapi import APIRouter

from app.api.deps import SessionDep
from app.core.rate_limit import rate_limit
from app.modules.app_credentials.deps import AppCredentialDep
from app.modules.audit.deps import AuditContextDep
from app.modules.rbac.manifest import ManifestService
from app.modules.rbac.schemas import ManifestPut, ManifestResult

router = APIRouter(prefix="/apps", tags=["app-manifest"])


@router.put(
    "/{key}/manifest",
    response_model=ManifestResult,
    dependencies=[rate_limit("app_manifest")],
    responses={
        401: {"description": "Missing or invalid app credential"},
        403: {
            "description": "Credential of another app (app_credential_mismatch), wrong scope "
            "(app_credential_scope) or disabled app"
        },
        409: {"description": "Flip, requires_staff or role not manifest-managed (details)"},
        422: {"description": "Invalid manifest (details[].reason)"},
        429: {"description": "Rate limited"},
    },
    summary="Push the app's permission manifest (app credential)",
)
async def put_manifest(
    session: SessionDep,
    ctx: AuditContextDep,
    caller: AppCredentialDep,
    key: str,
    body: ManifestPut,
) -> ManifestResult:
    return await ManifestService(session).sync(key, body, caller=caller, ctx=ctx)
