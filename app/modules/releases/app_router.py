"""The app's release write — R3 class 4 (a), `releases`-scoped app credential (ADM-034, ADR-0021).

Server-to-server only: no CORS, per-IP rate limit before authentication. The body is read by hand
(see `body.py`) after the credential check, never as a Pydantic body parameter.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Path, Request, Response, status

from app.api.deps import SessionDep
from app.core.rate_limit import rate_limit
from app.modules.app_credentials.deps import ReleasesCredentialDep
from app.modules.audit.deps import AuditContextDep
from app.modules.releases.app_write import ReleaseAppWriteService
from app.modules.releases.body import inline_schema, read_json_body
from app.modules.releases.models import ReleaseEnvironment
from app.modules.releases.schemas import ReleaseDetail, release_detail
from app.modules.releases.write_schemas import (
    ReleasePut,
    check_release_path,
    check_staging_sha,
)

router = APIRouter(prefix="/apps", tags=["app-releases"])

_OPENAPI: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {"application/json": {"schema": inline_schema(ReleasePut)}},
    }
}


@router.put(
    "/{key}/releases/{environment}/{version}",
    response_model=ReleaseDetail,
    dependencies=[rate_limit("app_release")],
    summary="Record a release of the app (releases-scoped app credential)",
    description=(
        "Creates the release as `pending` (201), or replaces it while it is `pending`, or, once "
        "`deployed`/`failed`, only fills fields that are still empty (200). A differing "
        "non-empty field gives 409 `release_not_pending`. Never changes `status`. The body is "
        "untrusted CI-reported data: render it as text; it is not verified. Server-to-server "
        "only, no CORS."
    ),
    responses={
        200: {"description": "Replaced, enriched or unchanged"},
        401: {"description": "Missing or invalid app credential"},
        403: {
            "description": "app_credential_mismatch, app_credential_scope or app_disabled",
        },
        409: {"description": "release_not_pending (details[].field names only)"},
        411: {"description": "Content-Length required"},
        413: {"description": "Body too large"},
        415: {"description": "Content-Type must be application/json"},
        422: {"description": "Invalid body or version (details have no values)"},
        429: {"description": "Rate limited"},
    },
    openapi_extra=_OPENAPI,
)
async def put_release(
    request: Request,
    response: Response,
    session: SessionDep,
    ctx: AuditContextDep,
    caller: ReleasesCredentialDep,
    key: str,
    environment: ReleaseEnvironment,
    version: Annotated[str, Path(max_length=32)],
) -> ReleaseDetail:
    settings = request.app.state.settings
    check_release_path(environment, version)
    body = await read_json_body(
        request,
        ReleasePut,
        cap=settings.app_release_max_bytes,
        timeout_s=settings.app_release_read_timeout_seconds,
    )
    check_staging_sha(environment, version, body)
    view, created = await ReleaseAppWriteService(session).put(
        caller, environment, version, body, ctx=ctx
    )
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return release_detail(view)
