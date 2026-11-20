"""Admin (Bearer) endpoints for the release log — R3 class 1, `admin:*` (ADM-033, ADR-0021)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import AwareDatetime

from app.api.deps import PageDep, SessionDep
from app.core.pagination import Page
from app.modules.admin_auth.deps import AdminDep, current_admin, require_permission
from app.modules.audit.deps import AuditContextDep
from app.modules.rbac.models import APP_KEY_RE
from app.modules.releases.models import ReleaseEnvironment, ReleaseStatus
from app.modules.releases.repository import ListFilter, ReleaseView
from app.modules.releases.schemas import (
    ReleaseChange,
    ReleaseDetail,
    ReleaseImage,
    ReleaseSort,
    ReleaseSummary,
)
from app.modules.releases.service import ReleaseService

router = APIRouter(prefix="/releases", tags=["releases"], dependencies=[Depends(current_admin)])

PERM = "admin:*"
_404 = "Unknown release"
AppFilter = Annotated[str | None, Query(pattern=APP_KEY_RE, description="App key.")]


def _summary(v: ReleaseView) -> ReleaseSummary:
    return ReleaseSummary.model_validate(v, from_attributes=True)


def _detail(v: ReleaseView) -> ReleaseDetail:
    return ReleaseDetail(
        **_summary(v).model_dump(),
        images=[ReleaseImage(**i) for i in v.images],
        changes=[ReleaseChange(**c) for c in v.changes],
    )


@router.get(
    "",
    response_model=Page[ReleaseSummary],
    description="Releases of every app, newest first (no `images`/`changes` bodies).",
    **require_permission(PERM),
)
async def list_releases(
    session: SessionDep,
    page: PageDep,
    app: AppFilter = None,
    environment: Annotated[ReleaseEnvironment | None, Query()] = None,
    status: Annotated[ReleaseStatus | None, Query()] = None,
    created_from: Annotated[
        AwareDatetime | None, Query(description="Inclusive, on `created_at`.")
    ] = None,
    created_to: Annotated[
        AwareDatetime | None, Query(description="Exclusive, on `created_at`.")
    ] = None,
    sort: Annotated[ReleaseSort, Query()] = ReleaseSort.CREATED_AT,
) -> Page[ReleaseSummary]:
    f = ListFilter(
        app_key=app,
        environment=None if environment is None else environment.value,
        status=None if status is None else status.value,
        created_from=created_from,
        created_to=created_to,
    )
    items, total = await ReleaseService(session).list(f, sort, limit=page.limit, offset=page.offset)
    return Page(
        items=[_summary(i) for i in items], total=total, limit=page.limit, offset=page.offset
    )


# Declared before `/{release_id}` so that "live" is not parsed as an id.
@router.get(
    "/live",
    response_model=Page[ReleaseSummary],
    description=(
        "The live release per app and environment: among `deployed` records, the one with the "
        "latest `deployed_at` (a redeploy or rollback overwrites it; ties go to the greatest "
        "id). Ordered by app key, then environment. An unknown app gives an empty page."
    ),
    **require_permission(PERM),
)
async def live_releases(
    session: SessionDep,
    page: PageDep,
    app: AppFilter = None,
    environment: Annotated[ReleaseEnvironment | None, Query()] = None,
) -> Page[ReleaseSummary]:
    items, total = await ReleaseService(session).live(
        app,
        None if environment is None else environment.value,
        limit=page.limit,
        offset=page.offset,
    )
    return Page(
        items=[_summary(i) for i in items], total=total, limit=page.limit, offset=page.offset
    )


@router.get(
    "/{release_id}",
    response_model=ReleaseDetail,
    **require_permission(PERM, errors={404: _404}),
)
async def get_release(session: SessionDep, release_id: uuid.UUID) -> ReleaseDetail:
    return _detail(await ReleaseService(session).get(release_id))


@router.delete(
    "/{release_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    description="Delete a wrong record (audited, ids only). A retried CI write may recreate it.",
    **require_permission(PERM, errors={404: _404}),
)
async def delete_release(
    session: SessionDep, principal: AdminDep, ctx: AuditContextDep, release_id: uuid.UUID
) -> Response:
    await ReleaseService(session).delete(release_id, actor=principal, ctx=ctx)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
