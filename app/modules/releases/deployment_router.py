"""The deployer's deploy report — R3 class 4 (b), `mmdk_` deployer credential (ADM-036, ADR-0021).

Server-to-server only: no CORS, per-IP rate limit before authentication. The body is read by hand
(see `body.py`) after the credential check, never as a Pydantic body parameter.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Path, Request, Response, status

from app.api.deps import SessionDep
from app.core.rate_limit import rate_limit
from app.modules.audit.deps import AuditContextDep
from app.modules.deployer_credentials.deps import DeployerDep
from app.modules.releases.body import inline_schema, read_json_body
from app.modules.releases.deployment import ReleaseDeploymentService
from app.modules.releases.models import ReleaseEnvironment
from app.modules.releases.schemas import ReleaseSummary, release_summary
from app.modules.releases.write_schemas import ReleaseDeploymentReport, check_release_path

router = APIRouter(prefix="/apps", tags=["release-deployments"])

_OPENAPI: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {"application/json": {"schema": inline_schema(ReleaseDeploymentReport)}},
    }
}


@router.put(
    "/{key}/releases/{environment}/{version}/deployment",
    response_model=ReleaseSummary,
    dependencies=[rate_limit("release_deployment")],
    summary="Report a deploy result (deployer credential)",
    description=(
        "Deploy result from the deployment repo: creates the record from the report (201) or "
        "applies `pending|failed -> deployed|failed` and redeploys (200). A retry with the same "
        "`deploy_run_url` is a no-op 200; `deployed -> failed` is 409 `release_already_deployed`. "
        "`deploy_run_url` is untrusted and only format-checked. Server-to-server only, no CORS."
    ),
    responses={
        200: {"description": "Updated or unchanged"},
        401: {"description": "Missing or invalid deployer credential"},
        404: {"description": "Unknown app"},
        409: {"description": "release_already_deployed, or a concurrent change (retry)"},
        411: {"description": "Content-Length required"},
        413: {"description": "Body too large"},
        415: {"description": "Content-Type must be application/json"},
        422: {"description": "Invalid body, version or deployed_at window (no values echoed)"},
        429: {"description": "Rate limited"},
    },
    openapi_extra=_OPENAPI,
)
async def report_deployment(
    request: Request,
    response: Response,
    session: SessionDep,
    ctx: AuditContextDep,
    caller: DeployerDep,
    key: str,
    environment: ReleaseEnvironment,
    version: Annotated[str, Path(max_length=32)],
) -> ReleaseSummary:
    settings = request.app.state.settings
    check_release_path(environment, version)
    body = await read_json_body(
        request,
        ReleaseDeploymentReport,
        cap=settings.release_deployment_max_bytes,
        timeout_s=settings.release_deployment_read_timeout_seconds,
    )
    view, created = await ReleaseDeploymentService(session).report(
        caller, key, environment, version, body, ctx=ctx
    )
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return release_summary(view)
