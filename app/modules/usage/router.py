"""Admin (Bearer) endpoints for usage metrics — R3 class 1 (ADM-039, ADR-0024)."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Path, Response, status

from app.api.deps import PageDep, SessionDep
from app.core.etag import ETAG_RESPONSE_HEADER, IF_MATCH_DESCRIPTION, parse_if_match, version_etag
from app.core.pagination import Page
from app.modules.admin_auth.deps import AdminDep, current_admin, require_permission
from app.modules.audit.deps import AuditContextDep
from app.modules.rbac.models import APP_KEY_RE
from app.modules.usage.models import UsageMetricStatus
from app.modules.usage.repository import UsageMetricView
from app.modules.usage.schemas import UsageMetricCreate, UsageMetricRead, UsageMetricUpdate
from app.modules.usage.service import UsageMetricService

router = APIRouter(
    prefix="/apps/{key}/usage-metrics", tags=["usage"], dependencies=[Depends(current_admin)]
)

READ = "admin:usage:read"
WRITE = "admin:usage:write"

_APP_404 = "Unknown app (the `admin` app has no usage metrics)"
_METRIC_404 = "Unknown app, or metric not of it"
_412 = "Stale If-Match (code precondition_failed); nothing changed; carries the current ETag"


def _with_etag(kwargs: dict[str, Any], code: int, description: str) -> dict[str, Any]:
    kwargs["responses"][code] = {
        "description": description,
        "headers": {"ETag": ETAG_RESPONSE_HEADER},
    }
    return kwargs


def _read(response: Response, v: UsageMetricView) -> UsageMetricRead:
    response.headers["ETag"] = version_etag(v.updated_at)
    return UsageMetricRead.model_validate(v)


@router.get(
    "",
    response_model=Page[UsageMetricRead],
    description="Metrics of the app, `key` order; all statuses unless `status` is given.",
    **require_permission(READ, errors={404: _APP_404}),
)
async def list_usage_metrics(
    session: SessionDep,
    page: PageDep,
    key: Annotated[str, Path(pattern=APP_KEY_RE)],
    status: UsageMetricStatus | None = None,
) -> Page[UsageMetricRead]:
    items, total = await UsageMetricService(session).list(
        key, status=status, limit=page.limit, offset=page.offset
    )
    return Page(
        items=[UsageMetricRead.model_validate(i) for i in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "",
    response_model=UsageMetricRead,
    status_code=status.HTTP_201_CREATED,
    **_with_etag(
        require_permission(
            WRITE, errors={404: _APP_404, 409: "Metric key already used (usage_metric_exists)"}
        ),
        201,
        "Created",
    ),
)
async def create_usage_metric(
    session: SessionDep,
    principal: AdminDep,
    ctx: AuditContextDep,
    key: Annotated[str, Path(pattern=APP_KEY_RE)],
    body: UsageMetricCreate,
    response: Response,
) -> UsageMetricRead:
    v = await UsageMetricService(session).create(key, body, actor=principal, ctx=ctx)
    return _read(response, v)


@router.get(
    "/{metric_id}",
    response_model=UsageMetricRead,
    **_with_etag(require_permission(READ, errors={404: _METRIC_404}), 200, "Successful Response"),
)
async def get_usage_metric(
    session: SessionDep,
    key: Annotated[str, Path(pattern=APP_KEY_RE)],
    metric_id: uuid.UUID,
    response: Response,
) -> UsageMetricRead:
    return _read(response, await UsageMetricService(session).get(key, metric_id))


@router.patch(
    "/{metric_id}",
    response_model=UsageMetricRead,
    description=(
        "Change name, default limit, period and/or status (`archived` / `active`, reversible). "
        "Changes apply to the running period at once. `key` and `unit` are immutable. "
        "A request that changes nothing writes nothing."
    ),
    **_with_etag(
        require_permission(WRITE, errors={404: _METRIC_404, 412: _412}), 200, "Successful Response"
    ),
)
async def update_usage_metric(
    session: SessionDep,
    principal: AdminDep,
    ctx: AuditContextDep,
    key: Annotated[str, Path(pattern=APP_KEY_RE)],
    metric_id: uuid.UUID,
    body: UsageMetricUpdate,
    response: Response,
    if_match: Annotated[
        list[str] | None, Header(alias="If-Match", description=IF_MATCH_DESCRIPTION)
    ] = None,
) -> UsageMetricRead:
    v = await UsageMetricService(session).update(
        key, metric_id, body, actor=principal, ctx=ctx, precondition=parse_if_match(if_match)
    )
    return _read(response, v)
